from __future__ import annotations

import asyncio
import io
from contextlib import asynccontextmanager

import cv2
import numpy as np
import requests
from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.models import (
    AlertEntry,
    CommandEntry,
    CommandRequest,
    ModeRequest,
    OriginConfigRequest,
    StatusResponse,
    TargetSelection,
    VideoFeedConfigRequest,
)
from app.services.alerts import AlertMailer
from app.services.detection import VisionEngine
from app.services.robot import RobotController
from app.services.state import SharedState
from app.time_utils import now_local


settings = get_settings()
state = SharedState(settings=settings)
robot = RobotController(settings)
mailer = AlertMailer(settings)
vision_engine = VisionEngine(settings, state, robot, mailer)
HIDDEN_ALERT_TYPES = {"Camera disconnected", "Backend error", "Robot not responding"}


def visible_alerts():
    return [entry for entry in list(state.alerts) if entry.alert_type not in HIDDEN_ALERT_TYPES]


def render_map_png() -> bytes:
    snapshot = state.estimator.snapshot(
        mode=state.mode,
        estimated_distance_m=state.tracking_status.target.distance_m if state.tracking_status else None,
        target_bearing_degrees=_bearing_from_direction(state.tracking_status.target.direction if state.tracking_status else None),
    )
    canvas = np.zeros((360, 360, 3), dtype=np.uint8)
    canvas[:] = (18, 20, 30)
    margin = 36
    all_points = list(snapshot.path)
    all_points.append(snapshot.robot_position)
    if snapshot.target_position:
        all_points.append(snapshot.target_position)
    all_points.extend(snapshot.turn_points)

    xs = [point[0] for point in all_points] or [0.0]
    ys = [point[1] for point in all_points] or [0.0]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    scale = min((canvas.shape[1] - margin * 2) / span_x, (canvas.shape[0] - margin * 2) / span_y)
    scale = max(scale, 4)

    def project(point: list[float]) -> tuple[int, int]:
        x = int(margin + (point[0] - min_x) * scale)
        y = int(canvas.shape[0] - margin - (point[1] - min_y) * scale)
        return x, y

    cv2.rectangle(canvas, (18, 18), (342, 342), (40, 50, 72), 1)
    for index in range(1, len(snapshot.path)):
        cv2.line(canvas, project(snapshot.path[index - 1]), project(snapshot.path[index]), (80, 180, 255), 2)

    for turn_point in snapshot.turn_points:
        cv2.circle(canvas, project(turn_point), 4, (255, 191, 105), -1)

    robot_pt = project(snapshot.robot_position)
    cv2.circle(canvas, robot_pt, 8, (0, 220, 120), -1)
    heading_radians = np.deg2rad(snapshot.heading_degrees)
    arrow_pt = (
        int(robot_pt[0] + np.cos(heading_radians) * 18),
        int(robot_pt[1] - np.sin(heading_radians) * 18),
    )
    cv2.arrowedLine(canvas, robot_pt, arrow_pt, (0, 220, 120), 2, tipLength=0.35)
    cv2.putText(canvas, "Robot", (robot_pt[0] + 10, robot_pt[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    if snapshot.target_position:
        target_pt = project(snapshot.target_position)
        cv2.circle(canvas, target_pt, 7, (0, 140, 255), -1)
        cv2.putText(canvas, "Target", (target_pt[0] + 10, target_pt[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    cv2.putText(canvas, f"Mode: {snapshot.mode.title()}", (16, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
    cv2.putText(canvas, f"Heading: {snapshot.heading_degrees:.1f}", (16, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
    if snapshot.estimated_distance_m is not None:
        cv2.putText(
            canvas,
            f"Distance: {snapshot.estimated_distance_m:.2f}m",
            (16, 72),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (230, 230, 230),
            1,
        )

    ok, encoded = cv2.imencode(".png", canvas)
    return encoded.tobytes() if ok else b""


def _bearing_from_direction(direction: str | None) -> float | None:
    if direction == "Left":
        return -25
    if direction == "Right":
        return 25
    if direction == "Centered":
        return 0
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    vision_engine.start()
    yield
    vision_engine.stop()


app = FastAPI(title=settings.app.title, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    with state.lock:
        return StatusResponse(
            robot_status="Online" if state.robot_connected else "Offline",
            camera_status="Online" if state.camera_connected else "Offline",
            detection_status=state.detection_status,
            backend_status=state.backend_status,
            mode=state.mode,
            tracking_state=state.tracking_status.state if state.tracking_status else "No Target Selected",
            last_updated=now_local(),
        )


@app.get("/api/objects")
async def get_objects() -> JSONResponse:
    with state.lock:
        detections = [item.model_dump() for item in state.detections]
    return JSONResponse(jsonable_encoder({"objects": detections, "updated_at": now_local().isoformat()}))


@app.get("/api/dashboard")
async def get_dashboard() -> JSONResponse:
    with state.lock:
        snapshot = state.estimator.snapshot(
            mode=state.mode,
            estimated_distance_m=state.tracking_status.target.distance_m if state.tracking_status else None,
            target_bearing_degrees=_bearing_from_direction(state.tracking_status.target.direction if state.tracking_status else None),
        )
        payload = {
            "status": {
                "robot_status": "Online" if state.robot_connected else "Offline",
                "camera_status": "Online" if state.camera_connected else "Offline",
                "detection_status": state.detection_status,
                "backend_status": state.backend_status,
                "mode": state.mode,
                "tracking_state": state.tracking_status.state if state.tracking_status else "No Target Selected",
                "last_updated": now_local(),
            },
            "tracking": state.tracking_status.model_dump() if state.tracking_status else {},
            "objects": [item.model_dump() for item in state.detections],
            "history": [entry.model_dump() for entry in list(state.command_history)[:20]],
            "alerts": [entry.model_dump() for entry in visible_alerts()[:12]],
            "map": snapshot.model_dump(),
            "refresh": {
                "dashboard_seconds": settings.vision.dashboard_refresh_seconds,
                "frame_seconds": settings.vision.still_frame_refresh_seconds,
            },
            "video_feed": {
                "enabled": state.live_stream_enabled,
            },
            "origin": {
                "latitude": settings.tracking.origin_latitude,
                "longitude": settings.tracking.origin_longitude,
            },
        }
    return JSONResponse(jsonable_encoder(payload))


@app.get("/api/tracking")
async def get_tracking() -> JSONResponse:
    with state.lock:
        tracking = state.tracking_status.model_dump() if state.tracking_status else {}
    return JSONResponse(jsonable_encoder(tracking))


@app.post("/api/track")
async def select_target(payload: TargetSelection) -> JSONResponse:
    vision_engine.clear_tracking_memory()
    with state.lock:
        state.selected_target_id = payload.detection_id
        state.selected_target_label = payload.label
        state.selected_target_type = payload.target_type
        state.selected_target_value = payload.target_value or payload.label
        state.mode = "tracking"
        state.tracking_status.state = "Searching"
        state.tracking_status.mode = "tracking"
    success, message = robot.safe_stop()
    state.robot_connected = success
    state.add_command(
        CommandEntry(
            timestamp=now_local(),
            command="S",
            reason=f"Mode switch to tracking for target {payload.label}",
            target_label=payload.label,
            estimated_distance_m=None,
            direction=None,
            mode="tracking",
            success=success,
        )
    )
    return JSONResponse(jsonable_encoder({"ok": success, "message": message}))


@app.post("/api/mode")
async def set_mode(payload: ModeRequest) -> JSONResponse:
    success, message = robot.safe_stop()
    with state.lock:
        state.mode = payload.mode
        state.robot_connected = success
        if payload.mode == "manual":
            vision_engine.clear_tracking_memory()
            state.selected_target_id = None
            state.selected_target_label = None
            state.selected_target_type = "object"
            state.selected_target_value = None
            state.tracking_status.state = "No Target Selected"
            state.tracking_status.target = state.tracking_status.target.__class__(robot_action="S")
        else:
            state.tracking_status.state = "Searching" if state.selected_target_label else "No Target Selected"
        state.tracking_status.mode = payload.mode
    state.add_command(
        CommandEntry(
            timestamp=now_local(),
            command="S",
            reason=f"Safety stop during mode switch to {payload.mode}",
            target_label=state.selected_target_label,
            estimated_distance_m=None,
            direction=None,
            mode=payload.mode,
            success=success,
        )
    )
    return JSONResponse(jsonable_encoder({"ok": success, "message": message}))


@app.post("/api/manual-command")
async def manual_command(payload: CommandRequest) -> JSONResponse:
    with state.lock:
        if state.mode != "manual":
            raise HTTPException(status_code=409, detail="Manual commands are allowed only in manual mode.")
    success, message = robot.send_command(payload.command)
    state.robot_connected = success
    movement_amount_m, rotation_degrees = robot.movement_metadata(payload.command)
    state.add_command(
        CommandEntry(
            timestamp=now_local(),
            command=payload.command,
            reason=payload.reason,
            target_label=state.selected_target_label,
            estimated_distance_m=state.tracking_status.target.distance_m if state.tracking_status else None,
            direction=state.tracking_status.target.direction if state.tracking_status else None,
            mode="manual",
            movement_amount_m=movement_amount_m,
            rotation_degrees=rotation_degrees,
            success=success,
        )
    )
    return JSONResponse(jsonable_encoder({"ok": success, "message": message}))


@app.post("/api/config/distance")
async def set_distance(payload: DistanceConfigRequest) -> JSONResponse:
    with state.lock:
        state.tracking_status.desired_distance_m = payload.desired_distance_m
    settings.tracking.desired_distance_m = payload.desired_distance_m
    return JSONResponse(jsonable_encoder({"ok": True, "desired_distance_m": payload.desired_distance_m}))


@app.post("/api/config/video-feed")
async def set_video_feed(payload: VideoFeedConfigRequest) -> JSONResponse:
    with state.lock:
        state.live_stream_enabled = payload.enabled
    settings.vision.live_stream_enabled = payload.enabled
    return JSONResponse(jsonable_encoder({"ok": True, "enabled": payload.enabled}))


@app.post("/api/config/origin")
async def set_origin(payload: OriginConfigRequest) -> JSONResponse:
    with state.lock:
        settings.tracking.origin_latitude = payload.latitude
        settings.tracking.origin_longitude = payload.longitude
        state.estimator.origin_latitude = payload.latitude
        state.estimator.origin_longitude = payload.longitude
    return JSONResponse(jsonable_encoder({"ok": True, "origin": payload.model_dump()}))


@app.get("/api/history")
async def get_history() -> JSONResponse:
    with state.lock:
        history = [entry.model_dump() for entry in list(state.command_history)]
    return JSONResponse(jsonable_encoder({"history": history}))


@app.get("/api/alerts")
async def get_alerts() -> JSONResponse:
    with state.lock:
        alerts = [entry.model_dump() for entry in visible_alerts()]
    return JSONResponse(jsonable_encoder({"alerts": alerts}))


@app.get("/api/map")
async def get_map() -> JSONResponse:
    png = render_map_png()
    with state.lock:
        state.latest_map_png = png
        snapshot = state.estimator.snapshot(
            mode=state.mode,
            estimated_distance_m=state.tracking_status.target.distance_m if state.tracking_status else None,
            target_bearing_degrees=_bearing_from_direction(state.tracking_status.target.direction if state.tracking_status else None),
        )
    return JSONResponse(jsonable_encoder({"snapshot": snapshot.model_dump(), "image": "/api/map/image"}))


@app.get("/api/map/image")
async def get_map_image() -> StreamingResponse:
    png = render_map_png()
    with state.lock:
        state.latest_map_png = png
    return StreamingResponse(io.BytesIO(png), media_type="image/png")


@app.get("/api/frame.jpg")
async def get_latest_frame() -> StreamingResponse:
    with state.lock:
        live_stream_enabled = state.live_stream_enabled
        frame = state.latest_frame_jpeg

    if not live_stream_enabled:
        blank = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(blank, "Live stream disabled", (150, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (220, 220, 220), 2)
        cv2.putText(blank, "Background detection remains active", (120, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 220, 255), 2)
        ok, encoded = cv2.imencode(".jpg", blank)
        frame = encoded.tobytes() if ok else b""
    elif not frame:
        blank = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(blank, "Camera offline", (180, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (220, 220, 220), 2)
        ok, encoded = cv2.imencode(".jpg", blank)
        frame = encoded.tobytes() if ok else b""
    return StreamingResponse(io.BytesIO(frame), media_type="image/jpeg")


@app.get("/api/camera-stream")
async def proxy_camera_stream() -> StreamingResponse:
    boundary = b"frame"

    async def frame_generator():
        while True:
            with state.lock:
                frame = state.latest_frame_jpeg
                live_stream_enabled = state.live_stream_enabled

            if not live_stream_enabled or not frame:
                blank = np.zeros((360, 640, 3), dtype=np.uint8)
                text = "Camera stream unavailable" if not frame else "Live stream disabled"
                cv2.putText(blank, text, (60, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (220, 220, 220), 2)
                ok, encoded = cv2.imencode(".jpg", blank)
                frame_bytes = encoded.tobytes() if ok else b""
            else:
                frame_bytes = frame

            if frame_bytes:
                yield b"--" + boundary + b"\r\n"
                yield b"Content-Type: image/jpeg\r\n"
                yield f"Content-Length: {len(frame_bytes)}\r\n\r\n".encode("utf-8")
                yield frame_bytes
                yield b"\r\n"

            await asyncio.sleep(1.0 / max(settings.vision.capture_fps, 1))

    return StreamingResponse(frame_generator(), media_type=f"multipart/x-mixed-replace; boundary={boundary.decode()}")


@app.post("/api/demo-alert")
async def demo_alert() -> JSONResponse:
    alert = AlertEntry(
        timestamp=now_local(),
        level="warning",
        alert_type="Object is Missing",
        description="Object is missing from camera frame.",
    )
    state.add_alert(alert)
    return JSONResponse(jsonable_encoder({"ok": True}))


@app.post("/api/demo-camera-alert")
async def demo_camera_alert() -> JSONResponse:
    alert = AlertEntry(
        timestamp=now_local(),
        level="warning",
        alert_type="Obstacle detected",
        description="Obstacle detected in camera feed.",
    )
    state.add_alert(alert)
    return JSONResponse(jsonable_encoder({"ok": True}))
