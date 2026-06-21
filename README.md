# AI Smart Surveillance Robot Dashboard

Professional full-stack robotics dashboard for an ESP32 motor-controller robot and ESP32-CAM livestream, with FastAPI, OpenCV, YOLO, target tracking, manual override, alerts, and a responsive web UI.

## Features

- Live annotated camera stream from `ESP32-CAM`
- YOLO-based object detection with confidence, estimated distance, and dominant color
- One-target tracking with automatic robot movement logic
- Manual mode with button and keyboard controls
- Safety stop logic for mode switches, camera failure, lost target, and backend errors
- Relative tracking map generated from command history
- Command history and alert feed
- Optional email alerts with captured frame attachments

## Folder Structure

```text
Ai robot web/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   └── services/
│       ├── alerts.py
│       ├── detection.py
│       ├── map_estimator.py
│       ├── robot.py
│       ├── state.py
│       └── tracking.py
├── static/
│   ├── app.js
│   ├── index.html
│   └── styles.css
├── config.yaml
└── requirements.txt
```

## Setup

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Update [config.yaml](./config.yaml) with your network IPs, YOLO model path, and email credentials.
4. Start the backend:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

5. Open `http://localhost:8000`.

## Notes

- The default YOLO model is `yolov8n.pt`. Ultralytics will download it automatically if needed.
- The tracking map is a relative estimate built from command history so it can later be upgraded with IMU, encoder, ultrasonic, or GPS inputs.
- Email alerts are disabled by default for safety and convenience. Enable them in `config.yaml`.
