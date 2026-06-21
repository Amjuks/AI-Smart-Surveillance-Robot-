from __future__ import annotations

from datetime import datetime

from app.config import Settings
from app.models import Detection


class TargetTracker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def choose_target(
        self,
        detections: list[Detection],
        selected_target_id: str | None,
        selected_target_label: str | None,
        selected_target_type: str,
        selected_target_value: str | None,
        previous_center_x: int | None,
    ) -> Detection | None:
        if not selected_target_label:
            return None

        if selected_target_type == "color":
            target_color = selected_target_value or selected_target_label
            candidates = [d for d in detections if d.dominant_color == target_color]
        else:
            candidates = [d for d in detections if d.label == selected_target_label]
        if not candidates:
            return None

        if selected_target_type == "object" and selected_target_id:
            for candidate in candidates:
                if candidate.detection_id == selected_target_id:
                    return candidate

        if previous_center_x is not None:
            candidates.sort(key=lambda d: abs(d.center[0] - previous_center_x))
            return candidates[0]

        candidates.sort(key=lambda d: d.area, reverse=True)
        return candidates[0]

    def direction_for_target(self, frame_width: int, center_x: int) -> str:
        midpoint = frame_width // 2
        offset = center_x - midpoint
        if offset < -self.settings.tracking.center_tolerance_px:
            return "Left"
        if offset > self.settings.tracking.center_tolerance_px:
            return "Right"
        return "Centered"

    def action_for_distance(self, distance_m: float | None) -> str:
        if distance_m is None:
            return "S"
        if distance_m > self.settings.tracking.forward_distance_threshold_m:
            return "F"
        if distance_m < self.settings.tracking.reverse_distance_threshold_m:
            return "B"
        return "S"
