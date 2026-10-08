import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace

import cv2
import numpy as np

from bootstrap_models import verify_model
from proctor_cv import PersistentEvents, ProctorCV, frame_signals, summarize_face


class EventsTest(unittest.TestCase):
    def test_tampered_model_fails_before_deserialization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yolov8n.pt"
            path.write_bytes(b"untrusted model")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                verify_model(path)

    def test_missing_models_fail_without_automatic_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "Missing local model"):
                ProctorCV(directory)

    def test_requires_duration_then_emits_once_and_rearms(self):
        events = PersistentEvents(2)
        self.assertEqual(events.update({"phone_present": True}, 0), [])
        self.assertEqual(events.update({"phone_present": True}, 1.99), [])
        self.assertEqual(events.update({"phone_present": True}, 2)[0]["type"], "phone_present")
        self.assertEqual(events.update({"phone_present": True}, 10), [])
        events.update({"phone_present": False}, 11)
        events.update({"phone_present": True}, 12)
        self.assertEqual(len(events.update({"phone_present": True}, 14)), 1)

    def test_invalid_timestamp(self):
        events = PersistentEvents()
        events.update({}, 10)
        for value in [9, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                events.update({}, value)

    def test_face_absence_and_second_face(self):
        self.assertTrue(frame_signals([], [], None, 640, 480)["face_absent"])
        self.assertTrue(frame_signals([], [{}, {}], None, 640, 480)["second_face"])

    def test_calibration_gates_gaze_and_phone_height(self):
        face = {"box": [200, 100, 400, 300],
                "head": {"yaw_deg": 20, "pitch_deg": 20, "roll_deg": 0},
                "gaze": {"horizontal": 0.7, "vertical": 0.7}}
        phone = {"box": [50, 100, 100, 250], "confidence": 0.8}
        signals = frame_signals([phone], [face], None, 640, 480)
        self.assertTrue(signals["phone_raised"])
        self.assertFalse(signals["gaze_down"])
        baseline = {"yaw_deg": 0, "pitch_deg": 0, "horizontal": 0.5, "vertical": 0.5}
        signals = frame_signals([phone], [face], baseline, 640, 480)
        self.assertTrue(signals["gaze_down"])
        self.assertTrue(signals["head_down"])
        self.assertTrue(signals["head_right"])
        phone["box"] = [50, 360, 100, 470]
        self.assertFalse(frame_signals([phone], [face], baseline, 640, 480)["phone_raised"])

    def test_positive_pitch_points_forward_axis_down_in_mediapipe_coordinates(self):
        landmarks = [SimpleNamespace(x=0.5, y=0.5, z=0)] * 478
        baseline = {"yaw_deg": 0, "pitch_deg": 0, "horizontal": 0.5, "vertical": 0.5}
        for angle, points_down in [(20, True), (-20, False)]:
            matrix = np.eye(4)
            matrix[:3, :3] = cv2.Rodrigues(np.array([np.deg2rad(angle), 0, 0]))[0]
            forward = matrix[:3, :3] @ np.array([0, 0, 1])
            self.assertEqual(forward[1] < 0, points_down)
            face = summarize_face(landmarks, matrix, 640, 480)
            self.assertEqual(frame_signals([], [face], baseline, 640, 480)["head_down"], points_down)

    def test_yaw_and_iris_geometry_use_unmirrored_frame_directions(self):
        landmarks = [SimpleNamespace(x=0.5, y=0.5, z=0) for _ in range(478)]
        for left, right, top, bottom in [(33, 133, 159, 145), (362, 263, 386, 374)]:
            landmarks[left].x, landmarks[right].x = 0.4, 0.6
            landmarks[top].y, landmarks[bottom].y = 0.48, 0.52
        for iris in [468, 473]:
            landmarks[iris].x, landmarks[iris].y = 0.55, 0.518
        matrix = np.eye(4)
        matrix[:3, :3] = cv2.Rodrigues(np.array([0, np.deg2rad(20), 0]))[0]
        self.assertGreater((matrix[:3, :3] @ np.array([0, 0, 1]))[0], 0)
        face = summarize_face(landmarks, matrix, 640, 480)
        baseline = {"yaw_deg": 0, "pitch_deg": 0, "horizontal": 0.5, "vertical": 0.5}
        signals = frame_signals([], [face], baseline, 640, 480)
        self.assertTrue(signals["head_right"])
        self.assertTrue(signals["gaze_right"])
        self.assertTrue(signals["gaze_down"])
        self.assertFalse(signals["gaze_left"])


if __name__ == "__main__":
    unittest.main()
