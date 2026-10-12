#!/usr/bin/env python3
"""Shizuku-backed read-only source for Android/data images.

Author: @dvkrum.
Uses official rish. Source files in Android/data are never modified.
A hidden shared-storage exchange file is created only for the duration of a read
and removed immediately afterwards.
"""
from __future__ import annotations
from contextlib import contextmanager
import os, secrets, shlex, subprocess, time
from pathlib import Path
from typing import Iterable
from photo_config import EXCHANGE_DIR

class ShizukuUnavailable(RuntimeError):
    """Transient Shizuku/rish outage; callers should preserve index and retry later."""


PREFIX = "shizuku://"
ANDROID_DATA = "/storage/emulated/0/Android/data"
RISH = Path(os.environ.get("PHOTO_INTELLIGENCE_RISH", str(Path.home() / "bin" / "rish")))
EXCHANGE_PREFIX = "exchange-"
_AVAILABILITY_CHECKED_AT = 0.0
_AVAILABILITY_VALUE = False


def is_virtual(value: str) -> bool:
    return str(value).startswith(PREFIX)

def to_virtual(path: str) -> str:
    if not path.startswith(ANDROID_DATA + "/"):
        raise ValueError("Shizuku source outside Android/data")
    return PREFIX + path

def to_absolute(value: str) -> str:
    if not is_virtual(value):
        raise ValueError("not a Shizuku source")
    path = value[len(PREFIX):]
    if not path.startswith(ANDROID_DATA + "/"):
        raise ValueError("invalid Shizuku source")
    return path

def _run(command: str, *, timeout: float = 30) -> subprocess.CompletedProcess:
    if not RISH.is_file():
        raise RuntimeError("rish unavailable")
    return subprocess.run([str(RISH), "-c", command],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=timeout, check=False)

def _prepare_exchange() -> None:
    EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
    # Best-effort cleanup of stale exchange files from interrupted reads.
    cutoff = time.time() - 3600
    try:
        for p in EXCHANGE_DIR.iterdir():
            if p.name.startswith(EXCHANGE_PREFIX):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                except OSError:
                    pass
    except OSError:
        pass

def _exchange_path() -> Path:
    _prepare_exchange()
    return EXCHANGE_DIR / f"{EXCHANGE_PREFIX}{secrets.token_hex(12)}.tmp"

def _remote_output(command: str, *, timeout: float = 60, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        target = _exchange_path()
        try:
            wrapped = f"( {command} ) > {shlex.quote(str(target))}"
            p = _run(wrapped, timeout=timeout)
            if p.returncode == 0 and target.is_file():
                return target.read_bytes()
            last = RuntimeError(f"rish exit {p.returncode}")
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            last = exc
        finally:
            try:
                target.unlink()
            except OSError:
                pass
        if attempt + 1 < attempts:
            time.sleep(0.35)
    raise ShizukuUnavailable("Shizuku exchange failed") from last

def available_cached(*, ttl: float = 30.0, force: bool = False, timeout: float = 3.0) -> bool:
    global _AVAILABILITY_CHECKED_AT, _AVAILABILITY_VALUE
    now = time.monotonic()
    if not force and _AVAILABILITY_CHECKED_AT and now - _AVAILABILITY_CHECKED_AT < ttl:
        return _AVAILABILITY_VALUE
    previously_available = _AVAILABILITY_VALUE
    probe = f"test -d {shlex.quote(ANDROID_DATA)}"
    try:
        result = _run(probe, timeout=max(0.5, float(timeout)))
        value = result.returncode == 0
    except (OSError, subprocess.SubprocessError, RuntimeError):
        value = False
    # Shizuku occasionally stalls for a few seconds under sustained gallery OCR.
    # Confirm a transition from READY to UNAVAILABLE once before deferring work.
    # Never retry force=True probes used to diagnose a specific failed materialization.
    if not value and previously_available and not force:
        time.sleep(0.15)
        try:
            value = _run(probe, timeout=max(0.5, min(5.0, float(timeout) + 2.0))).returncode == 0
        except (OSError, subprocess.SubprocessError, RuntimeError):
            value = False
    _AVAILABILITY_CHECKED_AT = time.monotonic()
    _AVAILABILITY_VALUE = value
    return value

def available() -> bool:
    return available_cached(ttl=0.0, force=True, timeout=8.0)

def source_available(value: str, *, ttl: float = 30.0) -> bool:
    return not is_virtual(value) or available_cached(ttl=ttl)

def enumerate_images(exts: Iterable[str], *, timeout: float = 120) -> list[tuple[str,int,float]]:
    exts = sorted({x.lower().lstrip(".") for x in exts if x})
    if not exts:
        return []
    tests = " -o ".join(f'-iname {shlex.quote("*."+ext)}' for ext in exts)
    manifest = _remote_output(
        f"find {shlex.quote(ANDROID_DATA)} -type f \\( {tests} \\) "
        r"-printf '%s\t%T@\t%p\0' 2>/dev/null",
        timeout=timeout, attempts=3)
    out: list[tuple[str,int,float]] = []
    for record in manifest.split(b"\0"):
        if not record:
            continue
        try:
            raw_size, raw_mtime, raw_path = record.split(b"\t", 2)
            path = raw_path.decode("utf-8", errors="surrogateescape")
            size = int(raw_size)
            mtime = float(raw_mtime)
        except (ValueError, OverflowError):
            continue
        if size > 0 and path.startswith(ANDROID_DATA + "/"):
            out.append((path,size,mtime))
    return out

def stat_virtual(value: str, *, timeout: float = 15) -> tuple[int,float]:
    path = to_absolute(value)
    raw = _remote_output(f"stat -c '%s %Y' -- {shlex.quote(path)}",
                         timeout=timeout, attempts=3)
    try:
        size_s, mtime_s = raw.decode().strip().split(None,1)
        return int(size_s), float(mtime_s)
    except Exception as exc:
        raise ValueError("invalid Shizuku stat") from exc

def validate(value: str, expected_size: int, expected_mtime: float) -> None:
    size, mtime = stat_virtual(value)
    if size != int(expected_size) or abs(mtime - float(expected_mtime)) > 1.1:
        raise ValueError("Shizuku source changed; rescan gallery")

@contextmanager
def materialize(value: str, expected_size: int, expected_mtime: float, state_dir: Path):
    del state_dir  # kept in signature for the generic gallery source abstraction
    validate(value, expected_size, expected_mtime)
    absolute = to_absolute(value)
    last: Exception | None = None
    target: Path | None = None
    try:
        for attempt in range(2):
            target = _exchange_path()
            try:
                p = _run(f"cp -- {shlex.quote(absolute)} {shlex.quote(str(target))}", timeout=180)
                if p.returncode == 0 and target.is_file() and target.stat().st_size == int(expected_size):
                    yield target
                    return
                last = ValueError("Shizuku read size mismatch")
            except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
                last = exc
            finally:
                if target is not None and target.exists():
                    try:
                        target.unlink()
                    except OSError:
                        pass
            if attempt == 0:
                time.sleep(0.35)
        if not available_cached(ttl=0.0, force=True, timeout=3.0):
            raise ShizukuUnavailable("Shizuku service unavailable") from last
        raise ValueError("Shizuku read failed") from last
    finally:
        if target is not None:
            try:
                target.unlink()
            except OSError:
                pass
