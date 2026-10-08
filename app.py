"""Loopback API for a local exam and camera-based review signals."""

import argparse
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

import cv2
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field


QUESTIONS = [
    {"id": "q1", "text": "Какой адрес доступен только на этом компьютере?",
     "options": {"A": "127.0.0.1", "B": "8.8.8.8", "C": "1.1.1.1"}},
    {"id": "q2", "text": "Что означает сигнал длительного отвода взгляда?",
     "options": {"A": "Доказанное нарушение", "B": "Причина для проверки человеком", "C": "Автоматическое отчисление"}},
    {"id": "q3", "text": "Где по умолчанию хранятся кадры этого прототипа?",
     "options": {"A": "В облаке", "B": "В журнале JSONL", "C": "Только в памяти, без записи"}},
]
CORRECT = {"q1": "A", "q2": "B", "q3": "C"}


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera_index: int = Field(default=0, ge=0, le=8, strict=True)


class AnswersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str] = Field(max_length=3)


class SecurityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(max_length=40)
    detail: str = Field(default="", max_length=240)


class LocalSession:
    def __init__(self, root, source=None, cv_factory=None, capture_factory=cv2.VideoCapture):
        self.root = Path(root)
        self.source = source
        self.cv_factory = cv_factory
        self.capture_factory = capture_factory
        self.lock = threading.RLock()
        self.stop_requested = threading.Event()
        self.worker = None
        self.phase = "idle"
        self.error = None
        self.session_id = None
        self.started = None
        self.stopped = None
        self.frame_time = None
        self.jpeg = None
        self.cv = None
        self.fps = 0.0
        self.event_counts = Counter()
        self.security_counts = Counter()
        self.calibration_requested = False
        self.calibration_reset = False
        self.answers = {}
        self.result = None
        self.journal = None
        self.recent_events = deque(maxlen=100)

    def elapsed(self):
        if self.started is None:
            return 0.0
        return round((self.stopped or time.monotonic()) - self.started, 3)

    def record(self, kind, **data):
        with self.lock:
            if self.journal is None:
                return
            row = {"session_id": self.session_id, "at": datetime.now(timezone.utc).isoformat(),
                   "elapsed_s": self.elapsed(), "kind": kind, **data}
            with self.journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            self.recent_events.append(row)

    def status(self):
        with self.lock:
            return {"phase": self.phase, "error": self.error, "session_id": self.session_id,
                    "elapsed_s": self.elapsed(), "fps": round(self.fps, 2),
                    "frame_age_s": None if self.frame_time is None else round(time.monotonic() - self.frame_time, 3),
                    "cv": self.cv, "calibrated": bool(self.cv and self.cv.get("calibrated")),
                    "calibration_samples": self.cv.get("calibration_samples", 0) if self.cv else 0,
                    "calibrating": self.calibration_requested,
                    "event_counts": dict(self.event_counts), "security_counts": dict(self.security_counts),
                    "recent_events": list(self.recent_events),
                    "journal": str(self.journal.relative_to(self.root)) if self.journal else None,
                    "source": "webcam" if self.source is None else "local_video",
                    "review_required": True, "frames_saved": False}

    def start(self, camera_index):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise HTTPException(409, "Session is already running or stopping")
            if self.source is not None and not Path(self.source).is_file():
                raise HTTPException(409, "The preselected local video does not exist")
            self.session_id = str(uuid.uuid4())
            self.started, self.stopped = time.monotonic(), None
            self.phase, self.error = "starting", None
            self.cv, self.jpeg, self.frame_time = None, None, None
            self.fps = 0.0
            self.event_counts.clear()
            self.security_counts.clear()
            self.recent_events.clear()
            self.answers, self.result = {}, None
            self.calibration_requested = self.calibration_reset = False
            self.stop_requested.clear()
            directory = self.root / "sessions"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.journal = directory / f"{self.session_id}.jsonl"
            descriptor = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            self.record("session_start", source="webcam" if self.source is None else "local_video")
            self.worker = threading.Thread(target=self.run, args=(camera_index,), daemon=True)
            self.worker.start()
        return self.status()

    def stop(self):
        self.stop_requested.set()
        worker = self.worker
        if worker:
            worker.join(timeout=2)
        with self.lock:
            if self.phase in {"running", "starting"}:
                # ponytail: an in-flight inference can finish after stop; discard its frame.
                self.phase = "stopping" if worker and worker.is_alive() else "stopped"
            self.calibration_requested = False
        return self.status()

    def calibrate(self):
        with self.lock:
            if self.phase != "running":
                raise HTTPException(409, "Start the camera or selected video before calibration")
            self.calibration_reset = self.calibration_requested = True
        return self.status()

    def run(self, camera_index):
        capture, detector = None, None
        try:
            if self.cv_factory is None:
                from proctor_cv import ProctorCV
                detector = ProctorCV(model_dir=self.root / "models")
            else:
                detector = self.cv_factory()
            if self.stop_requested.is_set():
                return
            capture = self.capture_factory(camera_index if self.source is None else str(self.source))
            if not capture.isOpened():
                raise RuntimeError("Cannot open webcam" if self.source is None else "Cannot open the selected video")
            if self.source is None:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            video_fps = capture.get(cv2.CAP_PROP_FPS) if self.source is not None else 0
            video_fps = video_fps if 0 < video_fps <= 240 else 25.0
            frame_number = 0
            previous_frame_time = None
            previous_signals = None
            with self.lock:
                self.phase = "running"
            while not self.stop_requested.is_set():
                loop_started = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    if self.source is None:
                        raise RuntimeError("Webcam stopped providing frames")
                    self.record("source_finished")
                    break
                height, width = frame.shape[:2]
                if max(width, height) > 960:
                    frame = cv2.resize(frame, (round(width * 960 / max(width, height)),
                                              round(height * 960 / max(width, height))))
                with self.lock:
                    reset = self.calibration_reset
                    self.calibration_reset = False
                if reset:
                    detector.reset_calibration()
                    self.record("calibration_started")
                source_time = frame_number / video_fps if self.source is not None else self.elapsed()
                frame_number += 1
                state = detector.process_frame(frame, timestamp_s=source_time)
                with self.lock:
                    if self.calibration_requested and detector.calibrate():
                        self.calibration_requested = False
                        self.record("calibration_completed")
                if self.stop_requested.is_set():
                    break
                preview = frame.copy()
                for phone in state.get("phones", []):
                    x1, y1, x2, y2 = phone["box"]
                    cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 170, 255), 2)
                    cv2.putText(preview, f"phone {phone['confidence']:.2f}", (x1, max(16, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 170, 255), 1)
                for face in state.get("faces", []):
                    x1, y1, x2, y2 = face["box"]
                    cv2.rectangle(preview, (x1, y1), (x2, y2), (100, 220, 130), 2)
                encoded, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not encoded:
                    raise RuntimeError("Cannot encode camera preview")
                with self.lock:
                    if self.stop_requested.is_set():
                        break
                    self.jpeg, self.cv, self.frame_time = jpeg.tobytes(), state, time.monotonic()
                    self.fps = 0.0 if previous_frame_time is None else 1 / max(self.frame_time - previous_frame_time, 0.001)
                    previous_frame_time = self.frame_time
                    signals = state.get("signals", {})
                    if signals != previous_signals:
                        self.record("signals_changed", signals=signals, source_time_s=source_time)
                        previous_signals = signals.copy()
                    for event in state.get("events", []):
                        self.event_counts[event["type"]] += 1
                        self.record("cv_event", event=event, review_required=True)
                if self.source is not None:
                    self.stop_requested.wait(max(0, 1 / video_fps - (time.monotonic() - loop_started)))
        except Exception as exc:
            with self.lock:
                self.phase, self.error = "error", f"{type(exc).__name__}: {exc}"
                self.record("runtime_error", error=self.error)
        finally:
            if capture is not None:
                capture.release()
            if detector is not None and hasattr(detector, "close"):
                try:
                    detector.close()
                except Exception as exc:
                    with self.lock:
                        self.phase, self.error = "error", f"{type(exc).__name__}: {exc}"
            with self.lock:
                self.stopped = time.monotonic()
                if self.phase != "error":
                    self.phase = "stopped"
                self.calibration_requested = False
                self.record("session_end", phase=self.phase, event_counts=dict(self.event_counts))


def create_app(token, root=None, source=None, cv_factory=None, capture_factory=cv2.VideoCapture):
    if not isinstance(token, str) or len(token) < 24 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("PROCTOR_TOKEN must contain at least 24 characters")
    root = Path(root or Path(__file__).parent).resolve()
    session = LocalSession(root, source, cv_factory, capture_factory)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        session.stop()

    app = FastAPI(title="Qostanai Local Proctor", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.session = session

    def authorize(request: Request):
        if not secrets.compare_digest(request.headers.get("authorization", "").encode("utf-8"), f"Bearer {token}".encode("ascii")):
            raise HTTPException(401, "A valid local bearer token is required")
        origin = request.headers.get("origin")
        # Electron loads local files; ordinary web pages cannot operate this API.
        if origin not in (None, "null"):
            raise HTTPException(403, "Web origins are not allowed")

    auth = [Depends(authorize)]

    @app.get("/health", dependencies=auth)
    def health():
        return {"ok": True, "camera_auto_start": False, "network": "loopback_only",
                "models_present": all((root / "models" / name).is_file()
                                      for name in ("yolov8n.pt", "face_landmarker.task"))}

    @app.get("/api/status", dependencies=auth)
    def status():
        return session.status()

    @app.post("/api/start", dependencies=auth)
    def start(payload: StartInput):
        return session.start(payload.camera_index)

    @app.post("/api/stop", dependencies=auth)
    def stop():
        return session.stop()

    @app.post("/api/calibrate", dependencies=auth)
    def calibrate():
        return session.calibrate()

    @app.get("/api/frame.jpg", dependencies=auth)
    def preview():
        with session.lock:
            jpeg = session.jpeg
        if jpeg is None:
            raise HTTPException(404, "No camera frame is available")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/exam", dependencies=auth)
    def exam():
        with session.lock:
            return {"title": "Демонстрационный тест", "questions": QUESTIONS,
                    "answers": session.answers.copy(), "submitted": session.result is not None,
                    "result": session.result}

    @app.post("/api/exam/answers", dependencies=auth)
    def answers(payload: AnswersInput):
        options = {question["id"]: question["options"] for question in QUESTIONS}
        if any(key not in options or value not in options[key] for key, value in payload.answers.items()):
            raise HTTPException(422, "Unknown question or answer option")
        with session.lock:
            if session.phase != "running" or session.result is not None:
                raise HTTPException(409, "Answers require an active, unsubmitted session")
            session.answers.update(payload.answers)
            return {"answers": session.answers.copy()}

    @app.post("/api/exam/submit", dependencies=auth)
    def submit():
        with session.lock:
            if session.session_id is None:
                raise HTTPException(409, "Start a local session first")
            if session.result is not None:
                return session.result
            if len(session.answers) != len(QUESTIONS):
                raise HTTPException(409, "Answer all demonstration questions first")
            session.result = {"score": sum(session.answers.get(key) == correct for key, correct in CORRECT.items()),
                              "total": len(QUESTIONS), "review_required": bool(session.event_counts) or any(
                                  session.security_counts[kind] for kind in ("focus_lost", "shortcut_blocked", "navigation_blocked", "fullscreen_left")),
                              "event_counts": dict(session.event_counts), "security_counts": dict(session.security_counts),
                              "automatic_violation_verdict": False}
            session.record("exam_submitted", answers=session.answers.copy(), result=session.result)
            return session.result

    @app.post("/api/security", dependencies=auth)
    def security(payload: SecurityInput):
        if payload.type not in {"focus_lost", "shortcut_blocked", "kiosk_started", "kiosk_stopped",
                                "navigation_blocked", "fullscreen_left"}:
            raise HTTPException(422, "Unknown security event")
        with session.lock:
            if session.session_id is None:
                raise HTTPException(409, "Start a session before recording security events")
            session.security_counts[payload.type] += 1
            session.record("security_event", event={"type": payload.type, "detail": payload.detail},
                           review_required=True)
        return {"recorded": True}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--video", type=Path, help="Preselect a local video; starts only after POST /api/start")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.video is not None and not args.video.is_file():
        parser.error("selected video must be an existing local file")
    token = os.environ.get("PROCTOR_TOKEN")
    if token is None:
        token = secrets.token_urlsafe(32)
        runtime = Path(__file__).parent / ".runtime.json"
        descriptor = os.open(runtime, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(runtime, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"token": token, "port": args.port}, stream)
        print("Local API token saved in .runtime.json (mode 600). Camera stays off until Start.")
    import uvicorn
    uvicorn.run(create_app(token, source=args.video), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
