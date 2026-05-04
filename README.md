# RollCount

RollCount is a classroom attendance prototype that combines face detection, face recognition, and attendance logging.

## MVP features

- Register students with a student ID, name, and reference image.
- Store student metadata locally in JSON.
- Open a webcam feed and detect faces in real time.
- Recognize students locally with OpenCV's LBPH face recognizer.
- Software has Daily, Weekly, or Monthly attendance log that tracks total attendance data.
- Fall back gracefully when optional ML dependencies are not installed.

## Project layout

```text
ROLLCOUNT/
├── app.py
├── requirements.txt
├── data/
│   ├── students/
│   └── attendance/
└── src/
    └── rollcount/
        ├── attendance.py
        ├── camera.py
        ├── config.py
        ├── detector.py
        ├── recognition.py
        ├── registry.py
        └── ui.py
```

## Setup

1. Create and activate a virtual environment.
2. Install the project dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Run the app:

```powershell
python app.py
```

## Notes about YOLO and recognition

- This MVP supports a YOLO face detector through `ultralytics` if you provide a model file path.
- If `models/yolo26n.pt` exists, the app will try to load it automatically at startup.
- If a YOLO model is not configured, the app falls back to OpenCV's Haar cascade face detector.
- Face recognition now uses OpenCV's LBPH recognizer trained from the registered student photos.
- Because LBPH is image-based, clearer front-facing student photos will improve recognition quality.
- If `yolo26n.pt` is not a face-trained model, detection quality may be poor, so a face-specific YOLO model is still recommended.

## Recommended next steps

- Replace the local JSON student store with SQLite or PostgreSQL.
- Add course and section support.
- Add a retraining or embedding pipeline instead of matching directly against ID photos.
- Export attendance to Canvas-compatible CSV formats.
