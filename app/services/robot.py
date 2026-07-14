from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock, Timer
from typing import Literal

import requests

from app.config import Settings


class RobotController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.command_names = {
            "F": "Forward",
            "B": "Backward",
            "L": "Left",
            "R": "Right",
            "S": "Stop",
        }
        self._last_command_sent_at = datetime.min
        self._last_command = "S"
        self._stop_timer: Timer | None = None
        self._timer_lock = Lock()

    @property
    def last_command(self) -> str:
        return self._last_command

    def send_command(self, command: Literal["F", "B", "L", "R", "S"], force: bool = False) -> tuple[bool, str]:
        now = datetime.utcnow()
        cooldown = timedelta(seconds=self.settings.tracking.command_cooldown_seconds)
        if not force and command == self._last_command and (now - self._last_command_sent_at) < cooldown:
            return True, "Suppressed duplicate command during cooldown"

        if not force and (now - self._last_command_sent_at) < cooldown and command != "S":
            return True, "Suppressed command during cooldown"

        try:
            response = requests.get(
                f"{self.settings.network.robot_base_url}/{command}",
                timeout=self.settings.network.request_timeout_seconds,
            )
            response.raise_for_status()
            self._last_command = command
            self._last_command_sent_at = now
            if command == "S":
                self._cancel_stop_timer()
            else:
                self._schedule_stop(command)
            return True, self.command_names.get(command, command)
        except requests.RequestException as exc:
            return False, str(exc)

    def _schedule_stop(self, command: Literal["F", "B", "L", "R"]) -> None:
        with self._timer_lock:
            self._cancel_stop_timer()
            duration = self._duration_for_command(command)
            if duration <= 0:
                return
            timer = Timer(duration, self.safe_stop)
            timer.daemon = True
            self._stop_timer = timer
            timer.start()

    def _cancel_stop_timer(self) -> None:
        if self._stop_timer is not None:
            self._stop_timer.cancel()
            self._stop_timer = None

    def _duration_for_command(self, command: Literal["F", "B", "L", "R", "S"]) -> float:
        if command == "F":
            return self.settings.tracking.forward_duration_seconds
        if command == "B":
            return self.settings.tracking.backward_duration_seconds
        if command == "L":
            return self.settings.tracking.left_duration_seconds
        if command == "R":
            return self.settings.tracking.right_duration_seconds
        return 0.0

    def movement_metadata(self, command: Literal["F", "B", "L", "R", "S"]) -> tuple[float | None, float | None]:
        if command == "F":
            return self.settings.tracking.forward_distance_m, None
        if command == "B":
            return self.settings.tracking.backward_distance_m, None
        if command == "L":
            return None, self.settings.tracking.left_turn_degrees
        if command == "R":
            return None, self.settings.tracking.right_turn_degrees
        return None, None

    def safe_stop(self) -> tuple[bool, str]:
        with self._timer_lock:
            self._cancel_stop_timer()
        return self.send_command("S", force=True)
