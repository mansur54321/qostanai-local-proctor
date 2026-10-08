"""Explicit setup-time download; inference never downloads models."""

import hashlib
import json
from pathlib import Path
import urllib.request


MODEL_URLS = {
    "yolov8n.pt": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}
MODEL_SHA256 = {
    "yolov8n.pt": "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36",
    "face_landmarker.task": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
}


def verify_model(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MODEL_SHA256[path.name]:
        raise ValueError(f"Model SHA256 mismatch: {path}")
    return digest


def main():
    directory = Path(__file__).resolve().parent / "models"
    directory.mkdir(exist_ok=True)
    manifest = {}
    for name, url in MODEL_URLS.items():
        target = directory / name
        if not target.is_file():
            temporary = target.with_suffix(target.suffix + ".download")
            try:
                urllib.request.urlretrieve(url, temporary)
                if temporary.stat().st_size < 1_000_000:
                    raise ValueError(f"Unexpectedly small model download: {name}")
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA256[name]:
                    raise ValueError(f"Model SHA256 mismatch: {name}")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        manifest[name] = {"url": url, "bytes": target.stat().st_size, "sha256": verify_model(target)}
        print(f"Ready: {target}")
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
