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
    forward_distance_m: float = 0.45
    backward_distance_m: float = 0.45
    left_turn_degrees: float = 14.0
    right_turn_degrees: float = 14.0
    origin_latitude: float | None = None
    origin_longitude: float | None = None
    start_heading_degrees: float = 0.0

    def update(self, command: str, max_route_points: int = 1000) -> None:
        if command == "L":
            self.heading_degrees = (self.heading_degrees + self.left_turn_degrees) % 360
            self.turn_points.append([round(self.x, 2), round(self.y, 2)])
            self.headings.append(round(self.heading_degrees, 1))
        elif command == "R":
            self.heading_degrees = (self.heading_degrees - self.right_turn_degrees) % 360
            self.turn_points.append([round(self.x, 2), round(self.y, 2)])
            self.headings.append(round(self.heading_degrees, 1))
        elif command == "F":
            distance = self.forward_distance_m
            radians = math.radians(self.heading_degrees)
            self.x += math.cos(radians) * distance
            self.y += math.sin(radians) * distance
            self.path.append([round(self.x, 2), round(self.y, 2)])
            self.headings.append(round(self.heading_degrees, 1))
        elif command == "B":
            distance = -self.backward_distance_m
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

    def current_geoposition(self) -> list[float] | None:
        if self.origin_latitude is None or self.origin_longitude is None:
            return None

        latitude_radians = math.radians(self.origin_latitude)
        meters_per_deg_lat = 111_320
        meters_per_deg_lon = 111_320 * math.cos(latitude_radians)
        latitude = self.origin_latitude + (self.y / meters_per_deg_lat)
        longitude = self.origin_longitude + (self.x / meters_per_deg_lon if meters_per_deg_lon != 0 else 0.0)
        return [round(latitude, 7), round(longitude, 7)]

    def origin_geoposition(self) -> list[float] | None:
        if self.origin_latitude is None or self.origin_longitude is None:
            return None
        return [round(self.origin_latitude, 7), round(self.origin_longitude, 7)]

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
            robot_geoposition=self.current_geoposition(),
            origin_geoposition=self.origin_geoposition(),
            target_position=target_position,
            heading_degrees=round(self.heading_degrees, 1),
            path=self.path,
            turn_points=self.turn_points,
            headings=self.headings,
            target_bearing_degrees=target_bearing_degrees,
            estimated_distance_m=estimated_distance_m,
        )
