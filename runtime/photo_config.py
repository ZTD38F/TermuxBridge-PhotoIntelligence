"""Runtime paths and constants for TermuxBridge Photo Intelligence.

Author: @dvkrum
"""
from __future__ import annotations
import os
from pathlib import Path

ADDON_NAME = "photo-intelligence"
ADDON_VERSION = "0.1.0"
ADDON_ABI = 1
BRIDGE_MAJOR = 1

HOME = Path(os.environ.get(
    "PHOTO_INTELLIGENCE_HOME",
    str(Path.home() / ".termux-mcp-bridge" / "addons" / ADDON_NAME),
)).expanduser().resolve()
STATE_DIR = Path(os.environ.get("PHOTO_INTELLIGENCE_STATE", str(HOME / "state"))).expanduser().resolve()
LOG_DIR = Path(os.environ.get("PHOTO_INTELLIGENCE_LOGS", str(HOME / "logs"))).expanduser().resolve()
CURRENT_DIR = Path(os.environ.get("PHOTO_INTELLIGENCE_CURRENT", str(HOME / "current"))).expanduser()
SHARED_ROOT = Path(os.environ.get("PHOTO_INTELLIGENCE_SHARED_ROOT", "/storage/emulated/0")).resolve()
DASHBOARD_HOST = os.environ.get("PHOTO_INTELLIGENCE_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("PHOTO_INTELLIGENCE_DASHBOARD_PORT", "18780"))
EXCHANGE_DIR = Path(os.environ.get(
    "PHOTO_INTELLIGENCE_EXCHANGE_DIR",
    str(SHARED_ROOT / ".termuxbridge-photo-intelligence"),
))
LEGACY_STATE_DIR = Path.home() / ".termux-mcp-bridge"

def ensure_dirs() -> None:
    for path in (HOME, STATE_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass
