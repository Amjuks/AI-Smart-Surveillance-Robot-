from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import requests
from ultralytics import YOLO

from app.config import Settings
from app.models import AlertEntry, CommandEntry, Detection, TrackingTarget
from app.services.alerts import AlertMailer
from app.services.robot import RobotController
from app.services.state import SharedState
from app.services.tracking import TargetTracker
from app.time_utils import now_local


@dataclass
class DetectionMemory:
    detection_id: str
    label: str
    confidence: float
    distance_m: float | None
    dominant_color: str
    bbox: list[int]
    center: list[int]
    area: int
    last_seen: object

    def as_detection(self, now: object, decay_per_second: float, stale: bool) -> Detection:
        age_seconds = max((now - self.last_seen).total_seconds(), 0.0)
        confidence = self.confidence if not stale else max(self.confidence - age_seconds * decay_per_second, 0.12)
        return Detection(
            detection_id=self.detection_id,
            label=self.label,
            confidence=round(confidence, 3),
            distance_m=self.distance_m,
            dominant_color=self.dominant_color,
            bbox=self.bbox,
            center=self.center,
            area=self.area,
            stale=stale,
            age_seconds=round(age_seconds, 2),
        )


class VisionEngine:
    def __init__(
        self,
        settings: Settings,
        state: SharedState,
        robot: RobotController,
        mailer: AlertMailer,
    ) -> None:
        self.settings = settings
        self.state = state
        self.robot = robot
        self.mailer = mailer
        self.tracker = TargetTracker(settings)
        self.capture_thread: threading.Thread | None = None
        self.inference_thread: threading.Thread | None = None
        self._running = False
        self._last_processed_at = 0.0
        self._detection_memory: list[DetectionMemory] = []
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._latest_frame_seq = 0
        self._latest_frame_at = 0.0
        self._tracked_template_gray: np.ndarray | None = None
        self._tracked_bbox: list[int] | None = None
        self._tracked_label: str | None = None
        self._tracked_color: str | None = None
        self._tracked_detection_id: str | None = None
        self._tracked_last_seen: object | None = None
        model_path = Path(self.settings.vision.model_weights)
        if not model_path.exists():
            raise FileNotFoundError(f"YOLO model weights not found: {model_path}")
        self.model = YOLO(str(model_path))

    def clear_tracking_memory(self) -> None:
        self._tracked_template_gray = None
        self._tracked_bbox = None
        self._tracked_label = None
        self._tracked_color = None
        self._tracked_detection_id = None
        self._tracked_last_seen = None

    def start(self) -> None:
        if self.capture_thread and self.capture_thread.is_alive():
            return
        self._running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True, name="camera-capture")
        self.inference_thread = threading.Thread(target=self._inference_loop, daemon=True, name="black-object-inference")
        self.capture_thread.start()
        self.inference_thread.start()

    def stop(self) -> None:
        self._running = False

    def _capture_loop(self) -> None:
        last_decoded_at = 0.0
        min_capture_interval = max(1.0 / max(self.settings.vision.capture_fps, 1), 0.01)

        while self._running:
            try:
                stream = requests.get(
                    self.settings.network.camera_stream_url,
                    stream=True,
                    timeout=self.settings.network.request_timeout_seconds,
                )
                stream.raise_for_status()
                bytes_buffer = b""

                for chunk in stream.iter_content(chunk_size=1024):
                    if not self._running:
                        break

                    bytes_buffer += chunk
                    start_marker = bytes_buffer.find(b"\xff\xd8")
                    end_marker = bytes_buffer.find(b"\xff\xd9")
                    if start_marker == -1 or end_marker == -1:
                        continue

                    jpg = bytes_buffer[start_marker : end_marker + 2]
                    bytes_buffer = bytes_buffer[end_marker + 2 :]
                    now_ts = time.time()
                    if now_ts - last_decoded_at < min_capture_interval:
                        continue

                    frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        continue

                    last_decoded_at = now_ts
                    with self._frame_lock:
                        self._latest_frame = frame
                        self._latest_frame_seq += 1
                        self._latest_frame_at = now_ts

                    with self.state.lock:
                        self.state.camera_connected = True
                        self.state.last_frame_at = now_local()
            except Exception as exc:
                with self.state.lock:
                    self.state.camera_connected = False
                    self.state.detection_status = "Error"
                    self.state.backend_status = "Online"
                    self.state.detections = []
                success, _ = self.robot.safe_stop()
                self._log_command("S", f"Safety stop: camera stream error ({exc})", None, success)
                self._raise_alert("critical", "Camera disconnected", f"Camera stream unavailable: {exc}", cooldown_seconds=30)
                time.sleep(self.settings.vision.camera_retry_seconds)

    def _inference_loop(self) -> None:
        frame_count = 0
        start_time = time.time()
        last_seen_seq = -1

        while self._running:
            with self._frame_lock:
                frame = None if self._latest_frame is None else self._latest_frame.copy()
                frame_seq = self._latest_frame_seq

            if frame is None or frame_seq == last_seen_seq:
                time.sleep(0.02)
                continue

            now_ts = time.time()
            if now_ts - self._last_processed_at < max(1.0 / max(self.settings.vision.target_fps, 1), 0.01):
                time.sleep(0.01)
                continue
            self._last_processed_at = now_ts
            last_seen_seq = frame_seq

            try:
                frame_count += 1
                elapsed = max(time.time() - start_time, 1e-6)
                fps = frame_count / elapsed

                raw_detections = self._detect_objects(frame)
                detections = self._stabilize_detections(raw_detections)
                live_detections = [item for item in detections if not item.stale]
                tracked_detection = self._handle_tracking(frame, live_detections)
                annotated = self._annotate_frame(frame, detections, tracked_detection, fps)
                ok, encoded = cv2.imencode(
                    ".jpg",
                    annotated,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.settings.vision.frame_jpeg_quality],
                )
                if not ok:
                    continue

                frame_jpeg = encoded.tobytes()
                raw_ok, raw_encoded = cv2.imencode(".jpg", frame)
                raw_jpeg = raw_encoded.tobytes() if raw_ok else frame_jpeg

                with self.state.lock:
                    self.state.latest_frame_jpeg = frame_jpeg
                    self.state.latest_frame_raw = raw_jpeg
                    self.state.detection_status = "Detecting" if live_detections else "Online"
                    self.state.detections = detections

                self._handle_safety_checks(tracked_detection)
            except Exception as exc:
                with self.state.lock:
                    self.state.detection_status = "Error"
                self._raise_alert("critical", "Backend error", f"Vision inference error: {exc}", cooldown_seconds=30)
                time.sleep(0.3)

    def _detect_objects(self, frame: np.ndarray) -> list[Detection]:
        detections: list[Detection] = []
        try:
            predictions = self.model.predict(
                source=frame,
                imgsz=640,
                conf=self.settings.vision.model_confidence,
                iou=self.settings.vision.model_iou_threshold,
                max_det=self.settings.vision.model_max_det,
                device=self.settings.vision.model_device,
                augment=False,
                verbose=False,
            )
        except Exception as exc:
            self._raise_alert("warning", "Detection error", f"Object detection failed: {exc}", cooldown_seconds=15)
            return []

        for result in predictions:
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                continue

            confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else boxes.conf.numpy()
            classes = boxes.cls.cpu().numpy() if hasattr(boxes.cls, "cpu") else boxes.cls.numpy()
            coords = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy.numpy()

            for cls, conf, xyxy in zip(classes, confs, coords):
                if conf < self.settings.vision.model_confidence:
                    continue
                x1, y1, x2, y2 = [int(round(x)) for x in xyxy]
                x1 = max(0, x1)
                y1 = max(0, y1)
                x2 = min(frame.shape[1], x2)
                y2 = min(frame.shape[0], y2)
                width = max(x2 - x1, 1)
                height = max(y2 - y1, 1)
                crop = frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else np.zeros((1, 1, 3), dtype=np.uint8)
                dominant_color = self._dominant_color_name(crop)
                label = self.model.names.get(int(cls), str(int(cls))) if hasattr(self.model, "names") else str(int(cls))
                distance_m = round(
                    (self.settings.tracking.distance_reference_width_px / width) * self.settings.tracking.desired_distance_m,
                    2,
                )
                detections.append(
                    Detection(
                        detection_id=str(uuid.uuid4())[:8],
                        label=label,
                        confidence=round(float(conf), 3),
                        distance_m=distance_m,
                        dominant_color=dominant_color,
                        bbox=[x1, y1, x2, y2],
                        center=[x1 + (width // 2), y1 + (height // 2)],
                        area=max(width * height, 1),
                    )
                )

        detections.sort(key=lambda item: item.confidence, reverse=True)
        return detections


    def _stabilize_detections(self, detections: list[Detection]) -> list[Detection]:
        now = now_local()
        used_memory_ids: set[str] = set()
        refreshed_memory: list[DetectionMemory] = []

        for detection in detections:
            matched = self._match_memory(detection, used_memory_ids)
            if matched:
                used_memory_ids.add(matched.detection_id)
                smoothed_bbox = self._smooth_bbox(matched.bbox, detection.bbox)
                smoothed_center = [(smoothed_bbox[0] + smoothed_bbox[2]) // 2, (smoothed_bbox[1] + smoothed_bbox[3]) // 2]
                matched.bbox = smoothed_bbox
                matched.center = smoothed_center
                matched.area = max((smoothed_bbox[2] - smoothed_bbox[0]) * (smoothed_bbox[3] - smoothed_bbox[1]), 1)
                matched.confidence = round((matched.confidence * 0.45) + (detection.confidence * 0.55), 3)
                matched.distance_m = detection.distance_m
                matched.dominant_color = detection.dominant_color
                matched.last_seen = now
                refreshed_memory.append(matched)
            else:
                refreshed_memory.append(
                    DetectionMemory(
                        detection_id=str(uuid.uuid4())[:8],
                        label=detection.label,
                        confidence=detection.confidence,
                        distance_m=detection.distance_m,
                        dominant_color=detection.dominant_color,
                        bbox=detection.bbox,
                        center=detection.center,
                        area=detection.area,
                        last_seen=now,
                    )
                )

        for memory in self._detection_memory:
            if memory.detection_id in used_memory_ids:
                continue
            age_seconds = (now - memory.last_seen).total_seconds()
            if age_seconds <= self.settings.vision.detection_hold_seconds:
                refreshed_memory.append(memory)

        deduped: dict[str, DetectionMemory] = {}
        for memory in refreshed_memory:
            existing = deduped.get(memory.detection_id)
            if not existing or memory.last_seen >= existing.last_seen:
                deduped[memory.detection_id] = memory

        self._detection_memory = list(deduped.values())
        visible = [
            memory.as_detection(
                now=now,
                decay_per_second=self.settings.vision.detection_confidence_decay_per_second,
                stale=(now - memory.last_seen).total_seconds() > 0.01,
            )
            for memory in self._detection_memory
            if (now - memory.last_seen).total_seconds() <= self.settings.vision.detection_hold_seconds
        ]
        visible.sort(key=lambda item: (item.stale, -item.area, item.age_seconds))
        return visible

    def _match_memory(self, detection: Detection, used_memory_ids: set[str]) -> DetectionMemory | None:
        best_match: DetectionMemory | None = None
        best_score = 0.0
        for memory in self._detection_memory:
            if memory.detection_id in used_memory_ids or memory.label != detection.label:
                continue
            iou = self._bbox_iou(memory.bbox, detection.bbox)
            center_distance = self._center_distance(memory.center, detection.center)
            if iou < self.settings.vision.detection_match_iou and center_distance > self.settings.vision.detection_center_match_px:
                continue
            score = iou + max(0.0, 1.0 - (center_distance / max(self.settings.vision.detection_center_match_px, 1)))
            if score > best_score:
                best_score = score
                best_match = memory
        return best_match

    def _smooth_bbox(self, previous: list[int], current: list[int]) -> list[int]:
        alpha = 0.6
        return [int((previous[index] * (1 - alpha)) + (current[index] * alpha)) for index in range(4)]

    def _bbox_iou(self, a: list[int], b: list[int]) -> float:
        x_left = max(a[0], b[0])
        y_top = max(a[1], b[1])
        x_right = min(a[2], b[2])
        y_bottom = min(a[3], b[3])
        if x_right <= x_left or y_bottom <= y_top:
            return 0.0
        intersection = (x_right - x_left) * (y_bottom - y_top)
        area_a = max((a[2] - a[0]) * (a[3] - a[1]), 1)
        area_b = max((b[2] - b[0]) * (b[3] - b[1]), 1)
        union = area_a + area_b - intersection
        return intersection / union if union else 0.0

    def _center_distance(self, a: list[int], b: list[int]) -> float:
        return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)

    def _handle_tracking(self, frame: np.ndarray, detections: list[Detection]) -> Detection | None:
        with self.state.lock:
            mode = self.state.mode
            selected_target_id = self.state.selected_target_id
            selected_target_label = self.state.selected_target_label
            selected_target_type = self.state.selected_target_type
            selected_target_value = self.state.selected_target_value
            previous_center_x = self.state.previous_target_center_x

        tracked = self.tracker.choose_target(
            detections,
            selected_target_id,
            selected_target_label,
            selected_target_type,
            selected_target_value,
            previous_center_x,
        )
        tracking_status = self.state.tracking_status
        if tracking_status is None:
            return tracked

        tracking_status.last_update = now_local()
        tracking_status.mode = mode

        if not selected_target_label:
            tracking_status.state = "No Target Selected"
            tracking_status.target = TrackingTarget(robot_action=self.robot.last_command, target_type=selected_target_type)
            return None

        if tracked is None:
            tracked = self._recover_target_with_template(frame, selected_target_label, selected_target_type, selected_target_value)

        if tracked is None:
            tracking_status.state = "Target Lost"
            tracking_status.target = TrackingTarget(
                label=selected_target_label,
                target_type=selected_target_type,
                target_value=selected_target_value,
                robot_action="S",
            )
            return None

        direction = self.tracker.direction_for_target(frame.shape[1], tracked.center[0])
        action = self._choose_robot_action(direction, tracked.distance_m)
        tracking_status.state = "Tracking Active" if mode == "tracking" else "Target Acquired"
        tracking_status.target = TrackingTarget(
            detection_id=tracked.detection_id,
            label=tracked.label,
            target_type=selected_target_type,
            target_value=selected_target_value or tracked.label,
            dominant_color=tracked.dominant_color,
            confidence=tracked.confidence,
            distance_m=tracked.distance_m,
            direction=direction,
            robot_action=action,
        )

        if previous_center_x is not None:
            movement = abs(tracked.center[0] - previous_center_x)
            if movement > self.settings.vision.suspicious_motion_threshold_pixels:
                self._raise_alert(
                    "warning",
                    "Suspicious movement detected",
                    "Large target displacement detected.",
                    cooldown_seconds=12,
                )

        with self.state.lock:
            self.state.last_target_seen_at = now_local()
            self.state.previous_target_center_x = tracked.center[0]

        self._update_tracker_memory(frame, tracked)

        if mode == "tracking":
            success, reason = self.robot.send_command(action)
            self._log_command(action, f"Tracking: {direction} / {reason}", tracked, success)
            with self.state.lock:
                self.state.robot_connected = success

        return tracked

    def _update_tracker_memory(self, frame: np.ndarray, detection: Detection) -> None:
        x1, y1, x2, y2 = detection.bbox
        x1 = max(x1, 0)
        y1 = max(y1, 0)
        x2 = min(x2, frame.shape[1])
        y2 = min(y2, frame.shape[0])
        if x2 - x1 < self.settings.vision.tracker_template_min_size_px:
            return
        if y2 - y1 < self.settings.vision.tracker_template_min_size_px:
            return

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return

        template_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if self._tracked_template_gray is None:
            self._tracked_template_gray = template_gray
        else:
            resized_previous = cv2.resize(self._tracked_template_gray, (template_gray.shape[1], template_gray.shape[0]))
            self._tracked_template_gray = cv2.addWeighted(resized_previous, 0.3, template_gray, 0.7, 0)
        self._tracked_bbox = [x1, y1, x2, y2]
        self._tracked_label = detection.label
        self._tracked_color = detection.dominant_color
        self._tracked_detection_id = detection.detection_id
        self._tracked_last_seen = now_local()

    def _recover_target_with_template(
        self,
        frame: np.ndarray,
        selected_target_label: str,
        selected_target_type: str,
        selected_target_value: str | None,
    ) -> Detection | None:
        if not self.settings.vision.tracker_fallback_enabled:
            return None
        if self._tracked_template_gray is None or self._tracked_bbox is None or self._tracked_last_seen is None:
            return None
        if (now_local() - self._tracked_last_seen).total_seconds() > self.settings.vision.tracker_max_age_seconds:
            return None

        x1, y1, x2, y2 = self._tracked_bbox
        padding = self.settings.vision.tracker_search_padding_px
        sx1 = max(x1 - padding, 0)
        sy1 = max(y1 - padding, 0)
        sx2 = min(x2 + padding, frame.shape[1])
        sy2 = min(y2 + padding, frame.shape[0])
        search = frame[sy1:sy2, sx1:sx2]
        if search.size == 0:
            return None

        search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
        template = self._tracked_template_gray
        if search_gray.shape[0] < template.shape[0] or search_gray.shape[1] < template.shape[1]:
            return None

        result = cv2.matchTemplate(search_gray, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, max_loc = cv2.minMaxLoc(result)
        if score < self.settings.vision.tracker_template_match_threshold:
            return None

        tx1 = sx1 + max_loc[0]
        ty1 = sy1 + max_loc[1]
        tx2 = tx1 + template.shape[1]
        ty2 = ty1 + template.shape[0]
        crop = frame[ty1:ty2, tx1:tx2]
        if crop.size == 0:
            return None

        dominant_color = self._dominant_color_name(crop)
        if selected_target_type == "color" and selected_target_value and dominant_color != selected_target_value:
            return None

        width = max(tx2 - tx1, 1)
        distance_m = round(
            (self.settings.tracking.distance_reference_width_px / width) * self.settings.tracking.desired_distance_m,
            2,
        )
        return Detection(
            detection_id=self._tracked_detection_id or str(uuid.uuid4())[:8],
            label=self._tracked_label or selected_target_label,
            confidence=round(float(score), 3),
            distance_m=distance_m,
            dominant_color=dominant_color,
            bbox=[tx1, ty1, tx2, ty2],
            center=[(tx1 + tx2) // 2, (ty1 + ty2) // 2],
            area=max((tx2 - tx1) * (ty2 - ty1), 1),
        )

    def _choose_robot_action(self, direction: str, distance_m: float | None) -> str:
        if direction == "Left":
            return "L"
        if direction == "Right":
            return "R"
        return self.tracker.action_for_distance(distance_m)

    def _annotate_frame(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        tracked_detection: Detection | None,
        fps: float,
    ) -> np.ndarray:
        annotated = frame.copy()
        for detection in detections:
            x1, y1, x2, y2 = detection.bbox
            is_tracked = tracked_detection and tracked_detection.detection_id == detection.detection_id
            color = (120, 120, 120) if detection.stale else ((0, 220, 120) if is_tracked else (0, 140, 255))
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            stale_label = " HOLD" if detection.stale else ""
            label = f"{detection.label}{stale_label} {detection.confidence:.2f} {detection.distance_m or 0:.2f}m"
            cv2.putText(annotated, label, (x1, max(y1 - 12, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            if is_tracked:
                cv2.drawMarker(
                    annotated,
                    (detection.center[0], detection.center[1]),
                    (255, 255, 255),
                    markerType=cv2.MARKER_CROSS,
                    markerSize=22,
                    thickness=2,
                )

        cv2.putText(
            annotated,
            f"FPS: {fps:.1f}",
            (20, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )
        return annotated

    def _handle_safety_checks(self, tracked_detection: Detection | None) -> None:
        now = now_local()
        with self.state.lock:
            mode = self.state.mode
            last_target_seen_at = self.state.last_target_seen_at
            selected_target_label = self.state.selected_target_label

        if mode == "tracking" and selected_target_label and tracked_detection is None:
            if last_target_seen_at and (now - last_target_seen_at).total_seconds() > self.settings.vision.max_lost_target_seconds:
                success, _ = self.robot.safe_stop()
                self._log_command("S", "Safety stop: target lost too long", None, success)
                self._raise_alert("warning", "Target lost", "Target missing for too long; robot stopped.", cooldown_seconds=10)
                with self.state.lock:
                    self.state.tracking_status.state = "Target Lost"

    def _dominant_color_name(self, crop: np.ndarray) -> str:
        if crop.size == 0:
            return "Unknown"

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mean_value = float(hsv[:, :, 2].mean())
        mean_saturation = float(hsv[:, :, 1].mean())
        if mean_value <= self.settings.vision.black_value_threshold and mean_saturation <= self.settings.vision.black_saturation_threshold:
            return "Black"
        if mean_value > 200 and mean_saturation < 35:
            return "White"
        if mean_saturation < 30:
            return "Grey"
        return "Mixed"

    def _log_command(self, command: str, reason: str, detection: Detection | None, success: bool) -> None:
        movement_amount_m, rotation_degrees = self.robot.movement_metadata(command)
        entry = CommandEntry(
            timestamp=now_local(),
            command=command,
            reason=reason,
            target_label=detection.label if detection else self.state.selected_target_label,
            estimated_distance_m=detection.distance_m if detection else None,
            direction=self.tracker.direction_for_target(640, detection.center[0]) if detection else None,
            mode=self.state.mode,
            movement_amount_m=movement_amount_m,
            rotation_degrees=rotation_degrees,
            success=success,
        )
        self.state.add_command(entry)

    def _raise_alert(self, level: str, alert_type: str, description: str, cooldown_seconds: int = 15) -> None:
        now = now_local()
        with self.state.lock:
            previous = self.state.last_alert_times.get(alert_type)
            if previous and (now - previous).total_seconds() < cooldown_seconds:
                return
            self.state.last_alert_times[alert_type] = now

        alert = AlertEntry(
            timestamp=now,
            level=level,
            alert_type=alert_type,
            description=description,
        )
        self.state.add_alert(alert)
        try:
            self.mailer.send_email_alert(
                subject=f"[Robot Alert] {alert_type}",
                body=f"{alert.timestamp.isoformat()}\n{description}",
                frame_bytes=self.state.latest_frame_raw,
                map_bytes=self.state.latest_map_png,
            )
        except Exception:
            pass
