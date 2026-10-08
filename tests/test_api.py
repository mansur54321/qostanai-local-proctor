import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import numpy as np
import pytest

from app import create_app


TOKEN = "test-token-only-local-not-for-production"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeCV:
    def __init__(self):
        self.samples = 0
        self.frames = 0

    def process_frame(self, frame, timestamp_s=None):
        self.frames += 1
        return {"phones": [], "faces": [], "face_count": 0,
                "signals": {"face_absent": True},
                "events": [{"type": "face_absent", "started_at": 0, "duration_s": 2}] if self.frames == 1 else [],
                "calibrated": self.samples >= 5, "calibration_samples": self.samples}

    def reset_calibration(self):
        self.samples = 0

    def calibrate(self):
        self.samples += 1
        return self.samples >= 5

    def close(self):
        pass


class FakeCapture:
    def __init__(self, source):
        self.source = source
        self.released = False

    def isOpened(self):
        return True

    def set(self, *_args):
        return True

    def get(self, *_args):
        return 25

    def read(self):
        time.sleep(0.01)
        return True, np.zeros((64, 64, 3), dtype=np.uint8)

    def release(self):
        self.released = True


def wait_for(client, predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = client.get("/api/status", headers=HEADERS).json()
        if predicate(status):
            return status
        time.sleep(0.01)
    raise AssertionError(f"Worker did not reach expected state: {status}")


def test_camera_consent_auth_and_source_validation(tmp_path):
    captures = []

    def capture(source):
        result = FakeCapture(source)
        captures.append(result)
        return result

    app = create_app(TOKEN, root=tmp_path, cv_factory=FakeCV, capture_factory=capture)
    with TestClient(app) as client:
        assert not captures
        assert client.get("/api/status").status_code == 401
        assert client.get("/health", headers={**HEADERS, "Origin": "https://evil.example"}).status_code == 403
        assert client.get("/health", headers=HEADERS).json()["camera_auto_start"] is False
        assert client.get("/api/frame.jpg", headers=HEADERS).status_code == 404
        for body in ({"camera_index": -1}, {"camera_index": 99}, {"camera_index": "0"},
                     {"camera_index": 0, "video": "/etc/passwd"}):
            assert client.post("/api/start", json=body, headers=HEADERS).status_code == 422
        assert not captures
        assert client.post("/api/start", json={"camera_index": 0}, headers=HEADERS).status_code == 200
        status = wait_for(client, lambda value: value["cv"] is not None)
        assert status["phase"] == "running"
        assert status["event_counts"] == {"face_absent": 1}
        assert captures[0].source == 0
        assert client.post("/api/start", json={}, headers=HEADERS).status_code == 409
        assert client.get("/api/frame.jpg", headers=HEADERS).headers["content-type"] == "image/jpeg"
        assert client.post("/api/stop", headers=HEADERS).json()["phase"] == "stopped"
        assert captures[0].released
    rows = [json.loads(line) for line in (tmp_path / status["journal"]).read_text().splitlines()]
    assert [row["kind"] for row in rows].count("cv_event") == 1
    assert rows[-1]["kind"] == "session_end"
    assert not list(tmp_path.rglob("*.jpg"))
    assert (tmp_path / status["journal"]).stat().st_mode & 0o777 == 0o600


def test_calibration_exam_and_security_are_real_state_transitions(tmp_path):
    app = create_app(TOKEN, root=tmp_path, cv_factory=FakeCV, capture_factory=FakeCapture)
    with TestClient(app) as client:
        assert client.post("/api/calibrate", headers=HEADERS).status_code == 409
        assert client.post("/api/exam/submit", headers=HEADERS).status_code == 409
        assert client.post("/api/security", json={"type": "focus_lost"}, headers=HEADERS).status_code == 409
        client.post("/api/start", json={}, headers=HEADERS)
        wait_for(client, lambda value: value["phase"] == "running")
        assert client.post("/api/calibrate", headers=HEADERS).status_code == 200
        status = wait_for(client, lambda value: value["calibrated"])
        assert status["calibration_samples"] == 5
        assert not status["calibrating"]
        assert client.post("/api/security", json={"type": "execute", "detail": "ls"}, headers=HEADERS).status_code == 422
        assert client.post("/api/security", json={"type": "shortcut_blocked", "detail": "Ctrl+C"}, headers=HEADERS).status_code == 200
        questions = client.get("/api/exam", headers=HEADERS).json()["questions"]
        assert len(questions) == 3 and "correct" not in questions[0]
        assert client.post("/api/exam/answers", json={"answers": {"q1": "Z"}}, headers=HEADERS).status_code == 422
        assert client.post("/api/exam/answers", json={"answers": {"unknown": "A"}}, headers=HEADERS).status_code == 422
        assert client.post("/api/exam/submit", headers=HEADERS).status_code == 409
        response = client.post("/api/exam/answers", json={"answers": {"q1": "A", "q2": "B", "q3": "C"}}, headers=HEADERS)
        assert response.status_code == 200
        result = client.post("/api/exam/submit", headers=HEADERS).json()
        assert result["score"] == result["total"] == 3
        assert result["review_required"] is True
        assert result["automatic_violation_verdict"] is False
        assert client.post("/api/exam/answers", json={"answers": {"q1": "B"}}, headers=HEADERS).status_code == 409
        assert client.post("/api/exam/submit", headers=HEADERS).json() == result
        client.post("/api/stop", headers=HEADERS)
    rows = [json.loads(line) for line in (tmp_path / status["journal"]).read_text().splitlines()]
    assert any(row["kind"] == "calibration_completed" for row in rows)
    assert any(row["kind"] == "exam_submitted" and row["result"]["score"] == 3 for row in rows)
    assert any(row["kind"] == "security_event" for row in rows)


def test_model_failure_does_not_open_camera(tmp_path):
    def broken_model():
        raise FileNotFoundError("Missing local model")

    opened = []
    app = create_app(TOKEN, root=tmp_path, cv_factory=broken_model,
                     capture_factory=lambda source: opened.append(source))
    with TestClient(app) as client:
        client.post("/api/start", json={}, headers=HEADERS)
        status = wait_for(client, lambda value: value["phase"] == "error")
        assert status["error"] == "FileNotFoundError: Missing local model"
        assert not opened


def test_recent_events_are_bounded_durable_and_reset_for_a_new_session(tmp_path):
    app = create_app(TOKEN, root=tmp_path, cv_factory=FakeCV, capture_factory=FakeCapture)
    with TestClient(app) as client:
        client.post("/api/start", json={}, headers=HEADERS)
        first = wait_for(client, lambda value: value["cv"] is not None)
        for index in range(105):
            assert client.post("/api/security", json={"type": "shortcut_blocked", "detail": str(index)}, headers=HEADERS).status_code == 200
        status = client.get("/api/status", headers=HEADERS).json()
        assert len(status["recent_events"]) == 100
        assert status["recent_events"][-1]["event"]["detail"] == "104"
        assert all(row["session_id"] == first["session_id"] for row in status["recent_events"])
        rows = [json.loads(line) for line in (tmp_path / first["journal"]).read_text().splitlines()]
        assert len(rows) > len(status["recent_events"])
        assert rows[-100:] == status["recent_events"]
        client.post("/api/stop", headers=HEADERS)
        client.post("/api/start", json={}, headers=HEADERS)
        second = client.get("/api/status", headers=HEADERS).json()
        assert second["session_id"] != first["session_id"]
        assert all(row["session_id"] == second["session_id"] for row in second["recent_events"])
        assert not any(row["kind"] == "security_event" for row in second["recent_events"])


def test_short_token_and_missing_selected_video_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least 24"):
        create_app("weak", root=tmp_path)
    app = create_app(TOKEN, root=tmp_path, source=tmp_path / "missing.mp4", cv_factory=FakeCV)
    with TestClient(app) as client:
        assert client.post("/api/start", json={}, headers=HEADERS).status_code == 409
        assert client.get("/api/status", headers=HEADERS).json()["phase"] == "idle"


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/frame.jpg", None),
    ("GET", "/api/exam", None),
    ("POST", "/api/start", {}),
    ("POST", "/api/stop", None),
    ("POST", "/api/calibrate", None),
    ("POST", "/api/exam/answers", {"answers": {"q1": "A"}}),
    ("POST", "/api/exam/submit", None),
    ("POST", "/api/security", {"type": "focus_lost"}),
])
def test_private_exam_and_camera_routes_require_token(tmp_path, method, path, body):
    app = create_app(TOKEN, root=tmp_path, cv_factory=FakeCV, capture_factory=FakeCapture)
    with TestClient(app) as client:
        assert client.request(method, path, json=body).status_code == 401
        assert app.state.session.session_id is None
