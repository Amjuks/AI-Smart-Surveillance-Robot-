from __future__ import annotations

from datetime import datetime, timedelta
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
            return True, self.command_names.get(command, command)
        except requests.RequestException as exc:
            return False, str(exc)

    def safe_stop(self) -> tuple[bool, str]:
        return self.send_command("S", force=True)
