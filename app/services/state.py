from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from app.config import Settings
from app.models import AlertEntry, CommandEntry, Detection, TrackingStatus, TrackingTarget
from app.services.map_estimator import RelativeMapEstimator
from app.time_utils import now_local


@dataclass
class SharedState:
    settings: Settings
    lock: threading.Lock = field(default_factory=threading.Lock)
    detections: list[Detection] = field(default_factory=list)
    latest_frame_jpeg: bytes | None = None
    latest_frame_raw: bytes | None = None
    latest_map_png: bytes | None = None
    last_frame_at: datetime | None = None
    camera_connected: bool = False
    robot_connected: bool = False
    detection_status: str = "Idle"
    backend_status: str = "Online"
    mode: str = "manual"
    tracking_status: TrackingStatus | None = None
    selected_target_label: str | None = None
    selected_target_id: str | None = None
    selected_target_type: str = "object"
    selected_target_value: str | None = None
    command_history: deque[CommandEntry] = field(default_factory=deque)
    alerts: deque[AlertEntry] = field(default_factory=deque)
    estimator: RelativeMapEstimator = field(default_factory=RelativeMapEstimator)
    last_target_seen_at: datetime | None = None
    suspicious_movement_flag: bool = False
    previous_target_center_x: int | None = None
    last_alert_times: dict[str, datetime] = field(default_factory=dict)
    live_stream_enabled: bool = True

    def __post_init__(self) -> None:
        self.live_stream_enabled = self.settings.vision.live_stream_enabled
        self.tracking_status = TrackingStatus(
            desired_distance_m=self.settings.tracking.desired_distance_m,
            last_update=now_local(),
        )
        self.estimator.forward_distance_m = self.settings.tracking.forward_distance_m
        self.estimator.backward_distance_m = self.settings.tracking.backward_distance_m
        self.estimator.left_turn_degrees = self.settings.tracking.left_turn_degrees
        self.estimator.right_turn_degrees = self.settings.tracking.right_turn_degrees
        self.estimator.origin_latitude = self.settings.tracking.origin_latitude
        self.estimator.origin_longitude = self.settings.tracking.origin_longitude
        self.estimator.start_heading_degrees = self.settings.tracking.start_heading_degrees
        self.estimator.heading_degrees = self.settings.tracking.start_heading_degrees

    def add_command(self, entry: CommandEntry) -> None:
        with self.lock:
            self.command_history.appendleft(entry)
            while len(self.command_history) > self.settings.history.max_commands:
                self.command_history.pop()
            self.estimator.update(entry.command, max_route_points=self.settings.history.max_route_points)

    def add_alert(self, entry: AlertEntry) -> None:
        with self.lock:
            self.alerts.appendleft(entry)
            while len(self.alerts) > self.settings.history.max_alerts:
                self.alerts.pop()
