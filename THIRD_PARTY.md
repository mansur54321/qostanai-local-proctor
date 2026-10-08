# Компоненты и источники

| Компонент | Использование | Источник / лицензия |
| --- | --- | --- |
| Ultralytics YOLOv8n | Детекция cell phone, CPU | https://github.com/ultralytics/ultralytics ; AGPL-3.0. Условия: https://www.ultralytics.com/license |
| YOLOv8n weights | Предобученная COCO модель | Официальный Ultralytics assets release, URL и SHA256 в `models/manifest.json`. AGPL-3.0, в составе открытого проекта. |
| MediaPipe | Face Landmarker, landmarks, head transform | https://github.com/google-ai-edge/mediapipe ; Apache-2.0. Модель из официального MediaPipe model storage, SHA256 в manifest. |
| PyTorch / torchvision | CPU runtime YOLO | https://github.com/pytorch/pytorch ; BSD-style. https://github.com/pytorch/vision ; BSD-3-Clause. |
| OpenCV | Камера, кадры, видео, аннотации | https://github.com/opencv/opencv ; Apache-2.0. |
| NumPy | Геометрия и калибровка | https://github.com/numpy/numpy ; BSD-3-Clause. |
| FastAPI / uvicorn / httpx | Локальный API и проверки | https://github.com/fastapi/fastapi ; MIT. https://github.com/encode/uvicorn и https://github.com/encode/httpx ; BSD-3-Clause. |
| Electron | Экзаменационное окно, IPC, kiosk | https://github.com/electron/electron ; MIT, дополнительные лицензии в дистрибутиве Electron. |
| pytest | Тесты, не обработка студентов | https://github.com/pytest-dev/pytest ; MIT. |

Конкретные версии закреплены в `requirements.txt` и `desktop/package-lock.json`. Сохраняйте лицензии пакетов при распространении собранного приложения.

Тестовые изображения и искусственное видео имеют собственные условия: [demo/fixtures/ATTRIBUTION.md](demo/fixtures/ATTRIBUTION.md). Public-domain портрет и CC BY 2.0 фотография не являются данными участников хакатона или экзамена.

Официальная техническая документация:

- https://docs.ultralytics.com/datasets/detect/coco/ (класс cell phone, отдельного класса face нет).
- https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/python (Face Landmarker и локальная модель).
- https://www.electronjs.org/docs/latest/api/global-shortcut (ограничения сочетаний клавиш и ОС).
- https://research.google/blog/mediapipe-iris-real-time-iris-tracking-depth-estimation/ (ориентиры радужки сами по себе не определяют, куда смотрит человек).
- https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc (ограничения Windows low-level hook, включая возможное снятие по таймауту).

Задание и Положение предоставлены пользователем в PDF. Их оригиналы не включены в публичный репозиторий.
