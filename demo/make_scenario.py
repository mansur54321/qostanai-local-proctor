"""Build an attributed artificial fixture video, not a recorded exam."""

from pathlib import Path

import cv2
import numpy as np


def main():
    fixtures = Path(__file__).resolve().parent / "fixtures"
    face = cv2.imread(str(fixtures / "face.jpg"))
    phone = cv2.imread(str(fixtures / "phone.jpg"))
    if face is None or phone is None:
        raise FileNotFoundError("Missing attributed fixture face.jpg or phone.jpg")
    single = np.full((480, 640, 3), 245, np.uint8)
    single[:, 128:512] = cv2.resize(face, (384, 480))
    raised = np.full_like(single, 245)
    raised[:, :384] = cv2.resize(face, (384, 480))
    raised[20:260, 400:640] = cv2.resize(phone, (240, 240))
    two = np.zeros_like(single)
    two[:400] = cv2.hconcat([cv2.resize(face, (320, 400))] * 2)
    blank = np.zeros_like(single)
    path = fixtures / "scenario.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (640, 480))
    if not writer.isOpened():
        raise RuntimeError("Could not create MJPEG fixture video")
    try:
        for frame, seconds in [(single, 10), (raised, 8), (two, 6), (blank, 6)]:
            for _ in range(seconds * 5):
                writer.write(frame)
    finally:
        writer.release()
    capture = cv2.VideoCapture(str(path))
    assert capture.isOpened() and int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 150
    capture.release()
    print(path)


if __name__ == "__main__":
    main()
