from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ModeType = Literal["manual", "tracking"]
TargetType = Literal["object", "color"]
TrackingStateType = Literal[
    "No Target Selected",
    "Searching",
    "Target Acquired",
    "Tracking Active",
    "Target Lost",
]


class Detection(BaseModel):
    detection_id: str
    label: str
    confidence: float
    distance_m: float | None = None
    dominant_color: str = "Unknown"
    bbox: list[int]
    center: list[int]
    area: int
    stale: bool = False
    age_seconds: float = 0.0
    target_type: TargetType = "object"


class TargetSelection(BaseModel):
    detection_id: str | None = None
    label: str
    target_type: TargetType = "object"
    target_value: str | None = None


class CommandRequest(BaseModel):
    command: Literal["F", "B", "L", "R", "S"]
    reason: str = "Manual control"


class ModeRequest(BaseModel):
    mode: ModeType


class DistanceConfigRequest(BaseModel):
    desired_distance_m: float = Field(gt=0.1, lt=10.0)


class OriginConfigRequest(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


class VideoFeedConfigRequest(BaseModel):
    enabled: bool


class CommandEntry(BaseModel):
    timestamp: datetime
    command: str
    reason: str
    target_label: str | None = None
    estimated_distance_m: float | None = None
    direction: str | None = None
    mode: ModeType
    movement_amount_m: float | None = None
    rotation_degrees: float | None = None
    success: bool = True


class AlertEntry(BaseModel):
    timestamp: datetime
    level: Literal["info", "warning", "critical"]
    alert_type: str
    description: str


class TrackingTarget(BaseModel):
    detection_id: str | None = None
    label: str | None = None
    target_type: TargetType = "object"
    target_value: str | None = None
    dominant_color: str | None = None
    confidence: float | None = None
    distance_m: float | None = None
    direction: str | None = None
    robot_action: str = "S"


class TrackingStatus(BaseModel):
    state: TrackingStateType = "No Target Selected"
    target: TrackingTarget = Field(default_factory=TrackingTarget)
    desired_distance_m: float
    mode: ModeType = "manual"
    last_update: datetime


class StatusResponse(BaseModel):
    robot_status: str
    camera_status: str
    detection_status: str
    backend_status: str
    mode: ModeType
    tracking_state: TrackingStateType
    last_updated: datetime


class MapPoint(BaseModel):
    x: float
    y: float
    kind: Literal["robot", "target", "path"]
    label: str


class MapSnapshot(BaseModel):
    mode: ModeType
    robot_position: list[float]
    robot_geoposition: list[float] | None = None
    origin_geoposition: list[float] | None = None
    target_position: list[float] | None = None
    heading_degrees: float = 0
    path: list[list[float]] = Field(default_factory=list)
    turn_points: list[list[float]] = Field(default_factory=list)
    headings: list[float] = Field(default_factory=list)
    target_bearing_degrees: float | None = None
    estimated_distance_m: float | None = None
