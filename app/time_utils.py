from __future__ import annotations

from datetime import datetime


def now_local() -> datetime:
    return datetime.now().astimezone()
