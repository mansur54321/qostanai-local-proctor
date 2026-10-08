"""Local phone and face inference; signals are review flags, not cheating verdicts."""

from collections import defaultdict
from pathlib import Path
import os
import time

import cv2
import mediapipe as mp
import numpy as np
import torch

os.environ["YOLO_OFFLINE"] = "true"
os.environ.setdefault("YOLO_CONFIG_DIR", str(Path(__file__).resolve().parent / ".cache" / "ultralytics"))
Path(os.environ["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
from ultralytics import YOLO

from bootstrap_models import verify_model


class PersistentEvents:
    """Emit once per continuous signal after the required duration."""

    def __init__(self, duration_s=2.0):
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")
        self.duration_s = duration_s
        self.started = {}
        self.emitted = set()
        self.last_timestamp = None

    def update(self, signals, timestamp_s):
        if not np.isfinite(timestamp_s):
            raise ValueError("timestamp_s must be finite")
        if self.last_timestamp is not None and timestamp_s < self.last_timestamp:
            raise ValueError("timestamp_s must be monotonic")
        self.last_timestamp = timestamp_s
        events = []
        for name, active in signals.items():
            if not active:
                self.started.pop(name, None)
                self.emitted.discard(name)
                continue
            self.started.setdefault(name, timestamp_s)
            duration = timestamp_s - self.started[name]
            if duration >= self.duration_s and name not in self.emitted:
                self.emitted.add(name)
                events.append({"type": name, "started_at": self.started[name],
                               "duration_s": round(duration, 3)})
        return events


def summarize_face(landmarks, matrix, width, height):
    points = np.array([[p.x, p.y, p.z] for p in landmarks], dtype=float)
    box = [int(np.clip(points[:, 0].min(), 0, 1) * width),
           int(np.clip(points[:, 1].min(), 0, 1) * height),
           int(np.clip(points[:, 0].max(), 0, 1) * width),
           int(np.clip(points[:, 1].max(), 0, 1) * height)]
    rotation = np.asarray(matrix, dtype=float)[:3, :3]
    rotation = rotation / np.linalg.norm(rotation, axis=0)
    pitch, yaw, roll = cv2.RQDecomp3x3(rotation)[0]
    gaze = []
    for iris, corners, lids in [(468, (33, 133), (159, 145)),
                                 (473, (362, 263), (386, 374))]:
        left, right = sorted(points[list(corners), 0])
        top, bottom = sorted(points[list(lids), 1])
        if right - left > 0.005 and bottom - top > 0.0025:
            gaze.append([(points[iris, 0] - left) / (right - left),
                         (points[iris, 1] - top) / (bottom - top)])
    gaze_mean = np.mean(gaze, axis=0) if gaze else [None, None]
    return {"box": box, "head": {"pitch_deg": round(float(pitch), 2),
                                   "yaw_deg": round(float(yaw), 2),
                                   "roll_deg": round(float(roll), 2)},
            "gaze": {"horizontal": None if gaze_mean[0] is None else round(float(gaze_mean[0]), 4),
                     "vertical": None if gaze_mean[1] is None else round(float(gaze_mean[1]), 4)},
            "landmark_count": len(landmarks)}


def frame_signals(phones, faces, baseline, width, height):
    signals = {name: False for name in ["face_absent", "second_face", "phone_present",
               "phone_raised", "gaze_down", "gaze_left", "gaze_right", "head_down",
               "head_left", "head_right"]}
    signals.update(face_absent=len(faces) == 0, second_face=len(faces) > 1,
                   phone_present=bool(phones))
    # ponytail: raised phone is geometry only; camera aim/action needs a trained dataset.
    if len(faces) == 1:
        face = faces[0]
        face_mid_y = (face["box"][1] + face["box"][3]) / 2
        signals["phone_raised"] = any(
            (p["box"][1] + p["box"][3]) / 2 <= face_mid_y + 0.12 * height
            and (p["box"][3] - p["box"][1]) >= 0.07 * height for p in phones)
        if baseline is not None:
            head = face["head"]
            yaw = head["yaw_deg"] - baseline["yaw_deg"]
            pitch = head["pitch_deg"] - baseline["pitch_deg"]
            signals.update(head_left=yaw < -18, head_right=yaw > 18, head_down=pitch > 15)
            gaze = face["gaze"]
            if gaze["horizontal"] is not None and gaze["vertical"] is not None:
                horizontal = gaze["horizontal"] - baseline["horizontal"]
                vertical = gaze["vertical"] - baseline["vertical"]
                signals.update(gaze_left=horizontal < -0.12, gaze_right=horizontal > 0.12,
                               gaze_down=vertical > 0.14)
    return signals


class ProctorCV:
    def __init__(self, model_dir="models", duration_s=2.0):
        model_dir = Path(model_dir)
        for name in ("yolov8n.pt", "face_landmarker.task"):
            if not (model_dir / name).is_file():
                raise FileNotFoundError(f"Missing local model: {model_dir / name}. Run bootstrap_models.py first.")
            verify_model(model_dir / name)
        torch.set_num_threads(2)
        self.yolo = YOLO(str(model_dir / "yolov8n.pt"))
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_dir / "face_landmarker.task")),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=3,
            min_face_detection_confidence=0.5, min_face_presence_confidence=0.5,
            output_facial_transformation_matrixes=True)
        self.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        self.events = PersistentEvents(duration_s)
        self.baseline = None
        self.calibration = defaultdict(list)
        self.last_faces = []
        self.last_ms = -1

    def process_frame(self, frame, timestamp_s=None):
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("frame must be a uint8 BGR image with shape (height, width, 3)")
        if frame.size == 0:
            raise ValueError("frame must not be empty")
        timestamp_s = time.monotonic() if timestamp_s is None else float(timestamp_s)
        if not np.isfinite(timestamp_s) or timestamp_s < 0:
            raise ValueError("timestamp_s must be finite and non-negative")
        if self.events.last_timestamp is not None and timestamp_s < self.events.last_timestamp:
            raise ValueError("timestamp_s must be monotonic")
        started = time.perf_counter()
        height, width = frame.shape[:2]
        predictions = self.yolo.predict(frame, classes=[67], conf=0.35, imgsz=640,
                                        device="cpu", verbose=False)[0]
        phones = [{"box": [int(v) for v in box.xyxy[0].tolist()],
                   "confidence": round(float(box.conf[0]), 4)} for box in predictions.boxes]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        milliseconds = max(self.last_ms + 1, int(timestamp_s * 1000))
        self.last_ms = milliseconds
        result = self.landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), milliseconds)
        faces = [summarize_face(points, matrix, width, height)
                 for points, matrix in zip(result.face_landmarks, result.facial_transformation_matrixes)]
        self.last_faces = faces
        signals = frame_signals(phones, faces, self.baseline, width, height)
        return {"phones": phones, "faces": faces, "face_count": len(faces), "signals": signals,
                "events": self.events.update(signals, timestamp_s), "timestamp_s": timestamp_s,
                "calibrated": self.baseline is not None,
                "calibration_samples": len(self.calibration["yaw_deg"]),
                "inference_ms": round((time.perf_counter() - started) * 1000, 1),
                "limitations": "Gaze/head are calibrated estimates in the unmirrored camera frame. Raised phone is a heuristic; camera aim and photography are not proven."}

    def calibrate(self, frame=None):
        """Collect one centered, eyes-open frame; at least five samples are needed."""
        if frame is not None:
            self.process_frame(frame)
        if len(self.last_faces) != 1:
            return False
        face = self.last_faces[0]
        if face["gaze"]["horizontal"] is None or face["gaze"]["vertical"] is None:
            return False
        for name, value in {**face["head"], **face["gaze"]}.items():
            self.calibration[name].append(value)
        if len(self.calibration["yaw_deg"]) >= 5:
            self.baseline = {name: float(np.median(values)) for name, values in self.calibration.items()}
        return self.baseline is not None

    def reset_calibration(self):
        self.calibration.clear()
        self.baseline = None
        self.events = PersistentEvents(self.events.duration_s)

    def close(self):
        self.landmarker.close()
