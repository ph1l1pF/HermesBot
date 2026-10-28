"""Persist only the selected environment, never API keys."""
import json
import os
import tempfile
from pathlib import Path

from .config import CARGOBOARD_URLS


def load_mode(path: Path, default: str) -> str:
    try:
        mode = json.loads(path.read_text()).get("environment")
    except (OSError, ValueError, AttributeError):
        return default
    return mode if isinstance(mode, str) and mode in CARGOBOARD_URLS else default


def save_mode(path: Path, mode: str) -> None:
    if mode not in CARGOBOARD_URLS:
        raise ValueError("Unknown environment")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".cargoboard-environment-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"environment": mode}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)
