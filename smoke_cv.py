"""Run actual local models on attributed static fixtures; no camera is opened."""

import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import statistics
from unittest.mock import patch


def source_hashes(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ["proctor_cv.py", "bootstrap_models.py", "smoke_cv.py"]}


def run_smoke(offline=False):
    import cv2
    import numpy as np
    from proctor_cv import ProctorCV

    root = Path(__file__).resolve().parent
    fixtures = root / "demo" / "fixtures"
    cv = ProctorCV(root / "models")
    results = []
    try:
        for timestamp, filename, expected_faces, expected_phone in [
            (0, "face.jpg", 1, False),
            (1, "phone.jpg", 0, True),
            (2, "two_faces.jpg", 2, False),
        ]:
            frame = cv2.imread(str(fixtures / filename))
            if frame is None:
                raise FileNotFoundError(fixtures / filename)
            result = cv.process_frame(frame, timestamp)
            assert result["face_count"] == expected_faces, result
            assert bool(result["phones"]) == expected_phone, result
            results.append({"fixture": filename, **result})
        face = cv2.imread(str(fixtures / "face.jpg"))
        for timestamp in range(3, 8):
            cv.process_frame(face, timestamp)
            cv.calibrate()
        assert cv.baseline is not None
        empty = np.zeros((480, 640, 3), dtype=np.uint8)
        cv.process_frame(empty, 8)
        absent = cv.process_frame(empty, 10)
        assert any(event["type"] == "face_absent" for event in absent["events"])
        results.append({"fixture": "generated_black_frame", **absent})
        output = {"test": "real_local_model_static_fixture_smoke", "passed": True,
                  "camera_opened": False, "training_performed": False,
                  "network_connect_blocked_before_cv_imports": offline,
                  "source_sha256": source_hashes(root),
                  "calibration_completed": cv.baseline is not None, "results": results,
                  "scope": "Static samples establish runnable inference, not accuracy on exam webcams."}
        (root / "artifacts").mkdir(exist_ok=True)
        (root / "artifacts" / "cv_smoke.json").write_text(json.dumps(output, indent=2) + "\n")
        print(json.dumps(output, indent=2))
    finally:
        cv.close()


def run_video_smoke(offline=False):
    import cv2
    from proctor_cv import ProctorCV

    root = Path(__file__).resolve().parent
    cv = ProctorCV(root / "models")
    capture = cv2.VideoCapture(str(root / "demo" / "fixtures" / "scenario.avi"))
    if not capture.isOpened():
        cv.close()
        raise FileNotFoundError("Missing scenario.avi; run demo/make_scenario.py")
    events, timings, phases = [], [], {}
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            result = cv.process_frame(frame, frame_index / 5)
            if frame_index < 5:
                cv.calibrate()
            timings.append(result["inference_ms"])
            events.extend(result["events"])
            phase = {0: "one_face", 50: "phone_raised", 90: "two_faces", 120: "empty"}.get(frame_index)
            if phase:
                phases[phase] = {name: result[name] for name in ["face_count", "phones", "signals"]}
            frame_index += 1
        assert frame_index == 150, frame_index
        assert {"phone_present", "phone_raised", "second_face", "face_absent"} <= {e["type"] for e in events}, events
        assert cv.baseline is not None
        output = {"test": "actual_CV_inference_artificial_video", "passed": True,
                  "frames": frame_index, "video_seconds": 30, "camera_opened": False,
                  "network_connect_blocked_before_cv_imports": offline,
                  "source_sha256": source_hashes(root), "calibrated": True,
                  "events": events, "phases": phases,
                  "inference_ms_median_after_first": round(statistics.median(timings[1:]), 1),
                  "inference_ms_min": min(timings), "inference_ms_max": max(timings),
                  "scope": "Constructed static source images validate execution and persistence. No webcam accuracy benchmark."}
        (root / "artifacts" / "cv_video_smoke.json").write_text(json.dumps(output, indent=2) + "\n")
        print(json.dumps(output, indent=2))
    finally:
        capture.release()
        cv.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Block socket.connect before importing CV libraries")
    parser.add_argument("--video", action="store_true", help="Also check all 150 frames of the attributed fixture video")
    args = parser.parse_args()
    context = patch("socket.socket.connect", side_effect=AssertionError("Network access attempted during offline CV smoke"))
    with context if args.offline else nullcontext():
        run_smoke(args.offline)
        if args.video:
            run_video_smoke(args.offline)


if __name__ == "__main__":
    main()
