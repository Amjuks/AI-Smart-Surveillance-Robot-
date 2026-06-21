from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.models import MapSnapshot


@dataclass
class RelativeMapEstimator:
    x: float = 0.0
    y: float = 0.0
    heading_degrees: float = 0.0
    path: list[list[float]] = field(default_factory=lambda: [[0.0, 0.0]])
    turn_points: list[list[float]] = field(default_factory=list)
    headings: list[float] = field(default_factory=lambda: [0.0])
    step_distance: float = 0.45
    turn_degrees: float = 14.0

    def update(self, command: str, max_route_points: int = 1000) -> None:
        if command == "L":
            self.heading_degrees = (self.heading_degrees + self.turn_degrees) % 360
            self.turn_points.append([round(self.x, 2), round(self.y, 2)])
            self.headings.append(round(self.heading_degrees, 1))
        elif command == "R":
            self.heading_degrees = (self.heading_degrees - self.turn_degrees) % 360
            self.turn_points.append([round(self.x, 2), round(self.y, 2)])
            self.headings.append(round(self.heading_degrees, 1))
        elif command in {"F", "B"}:
            distance = self.step_distance if command == "F" else -self.step_distance
            radians = math.radians(self.heading_degrees)
            self.x += math.cos(radians) * distance
            self.y += math.sin(radians) * distance
            self.path.append([round(self.x, 2), round(self.y, 2)])
            self.headings.append(round(self.heading_degrees, 1))

        if len(self.path) > max_route_points:
            self.path = self.path[-max_route_points:]
        if len(self.turn_points) > max_route_points:
            self.turn_points = self.turn_points[-max_route_points:]
        if len(self.headings) > max_route_points:
            self.headings = self.headings[-max_route_points:]

    def snapshot(
        self,
        mode: str,
        estimated_distance_m: float | None,
        target_bearing_degrees: float | None,
    ) -> MapSnapshot:
        target_position = None
        if estimated_distance_m is not None and target_bearing_degrees is not None:
            total_bearing = math.radians((self.heading_degrees + target_bearing_degrees) % 360)
            tx = self.x + math.cos(total_bearing) * estimated_distance_m
            ty = self.y + math.sin(total_bearing) * estimated_distance_m
            target_position = [round(tx, 2), round(ty, 2)]

        return MapSnapshot(
            mode=mode,
            robot_position=[round(self.x, 2), round(self.y, 2)],
            target_position=target_position,
            heading_degrees=round(self.heading_degrees, 1),
            path=self.path,
            turn_points=self.turn_points,
            headings=self.headings,
            target_bearing_degrees=target_bearing_degrees,
            estimated_distance_m=estimated_distance_m,
        )
