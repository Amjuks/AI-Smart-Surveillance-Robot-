from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class AppConfig(BaseModel):
    title: str = "AI Smart Surveillance Robot Dashboard"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


class NetworkConfig(BaseModel):
    robot_base_url: str
    camera_stream_url: str
    request_timeout_seconds: float = 2.0


class VisionConfig(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    black_value_threshold: int = 60
    black_saturation_threshold: int = 110
    min_black_area_px: int = 900
    morph_kernel_size: int = 5
    target_fps: int = 12
    frame_jpeg_quality: int = 80
    live_stream_enabled: bool = True
    object_refresh_seconds: int = 5
    max_lost_target_seconds: int = 4
    suspicious_motion_threshold_pixels: int = 120
    camera_retry_seconds: int = 4
    still_frame_refresh_seconds: float = 1.5
    dashboard_refresh_seconds: int = 3
    detection_hold_seconds: float = 3.5
    detection_match_iou: float = 0.28
    detection_center_match_px: int = 120
    detection_confidence_decay_per_second: float = 0.18
    capture_fps: int = 6
    tracker_fallback_enabled: bool = True
    tracker_template_match_threshold: float = 0.58
    tracker_search_padding_px: int = 140
    tracker_max_age_seconds: float = 2.2
    tracker_template_min_size_px: int = 24


class TrackingConfig(BaseModel):
    desired_distance_m: float = 1.8
    distance_reference_width_px: float = 180
    stop_tolerance_m: float = 0.2
    forward_distance_threshold_m: float = 2.1
    reverse_distance_threshold_m: float = 1.2
    center_tolerance_px: int = 70
    command_cooldown_seconds: float = 0.6


class AlertsConfig(BaseModel):
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = ""
    to_email: str = ""


class HistoryConfig(BaseModel):
    max_commands: int = 200
    max_alerts: int = 100
    max_frames_for_debug: int = 5
    max_route_points: int = 1000


class Settings(BaseModel):
    app: AppConfig
    network: NetworkConfig
    vision: VisionConfig
    tracking: TrackingConfig
    alerts: AlertsConfig
    history: HistoryConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    raw = _load_yaml(config_path)
    return Settings(**raw)
