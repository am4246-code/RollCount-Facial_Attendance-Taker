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
# Step by Step Guide

 1) Type in Student ID and Full Name, then click on "Take Live Pictures"

<img width="300" height="300" alt="image" src="https://github.com/user-attachments/assets/acfb438d-f1c7-4327-9beb-08b7dd720ae5" />

## 2) Click on "Start Face Scan" and wait until the program takes 3 pictures; Once completed, a confirmation screen should pop up.

<table>
  <tr>
    <td align="center">
      <img width="300" alt="Start Face Scan" src="https://github.com/user-attachments/assets/22b95f00-dc1c-4a88-b07d-8078f5c67c6f" />
    </td>
    <td align="center" style="font-size: 50px;">➡️</td>
    <td align="center">
      <img width="450" alt="Capture Complete" src="https://github.com/user-attachments/assets/ce8376e4-32bf-4ba3-8610-c19d541082b4" />
    </td>
  </tr>
</table>

## 3) Go to "Take Attendance" section, then start the livestream (detection model needs to load first before it starts detecting faces)

<img width="1428" height = "800" alt="image" src="https://github.com/user-attachments/assets/7a745f60-4bb9-451b-a093-11c4252925af" />

## 4) Wait untl the model detects your face (click the checkmark icon if it correctly matches the face of registered student; click 'X' if it doesn't).

<img width="1428" height="800" alt="image" src="https://github.com/user-attachments/assets/4673d2ce-f075-4f2d-9e99-b54232c0ac8f" />

## 5) Select the "Daily Log" to see today's attendance, "Weekly Log" to view the attendance log throughout the week, or the "Monthly Log" to view the attendance log throughout the month.

## (If you want to export this data on Canvas or Google Excel, click on "Export for Canvas (CSV)" button).

<img width="1442" height="262" alt="image" src="https://github.com/user-attachments/assets/d499ab22-6c0d-4037-884d-758000bbc1ad" />

<img width="1442" height="262" alt="image" src="https://github.com/user-attachments/assets/517de3d3-62a1-478e-9497-cee14095b9eb" />

<img width="1442" height="262" alt="image" src="https://github.com/user-attachments/assets/e4cc84ec-5c82-4d72-bfc6-c87b7e607769" />

# Quick Notes

- The program changes the days, weeks, and months automatically; the test images were taken on the dates shown in the picture.
- For this trial example, online images are being used. However, RollCount is normally used on people, not phone pictures.

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
