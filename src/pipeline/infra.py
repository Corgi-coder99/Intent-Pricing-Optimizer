"""Shared infrastructure: directory layout, atomic file writes, lock against overlapping runs."""
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def landing(self) -> Path: return self.root / "landing"        # where source files are dropped (never modified)
    @property
    def bronze(self) -> Path: return self.root / "bronze"          # raw immutable copies, named by hash
    @property
    def silver(self) -> Path: return self.root / "silver"          # typed, validated, deduplicated
    @property
    def gold(self) -> Path: return self.root / "gold"              # tables ready for the model and dashboard
    @property
    def quarantine(self) -> Path: return self.root / "quarantine"  # rejected rows with reasons
    @property
    def state(self) -> Path: return self.root / "state"            # manifest, fingerprint, lock

    def ensure(self) -> "Layout":
        for p in (self.landing, self.bronze, self.silver, self.gold, self.quarantine, self.state):
            p.mkdir(parents=True, exist_ok=True)
        return self


def _tmp_name(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = _tmp_name(path)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode("utf-8"))


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    tmp = _tmp_name(path)
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(paths: list) -> str:
    """Fingerprint of a set of input files; changes when any content changes."""
    h = hashlib.sha256()
    for p in sorted(Path(x) for x in paths):
        h.update(p.name.encode())
        h.update(sha256_file(p).encode() if p.exists() else b"missing")
    return h.hexdigest()


class PipelineBusy(RuntimeError):
    pass


class RunLock:
    """Pipeline-wide lock: only one run at a time, using an OS-level exclusive file lock (msvcrt on Windows, fcntl elsewhere).

    The operating system releases the lock automatically when the owning process dies, so there is no stale-lock reclaim
    race and no TTL. Works on local filesystems only (see docs/SECURITY.md). `ttl` is accepted for backward compatibility and unused.
    """

    def __init__(self, path: Path, ttl: int = 600, wait: float = 0.0):
        self.path, self.wait = Path(path), wait
        self._fd: Optional[int] = None

    def _try_lock(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        try:                                                  # diagnostics only
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, json.dumps({"pid": os.getpid(), "ts": time.time()}).encode())
        except OSError:
            pass
        return True

    def acquire(self) -> None:
        deadline = time.monotonic() + self.wait
        while not self._try_lock():
            if time.monotonic() >= deadline:
                raise PipelineBusy(f"Pipeline is already running (lock: {self.path})")
            time.sleep(0.2)

    def heartbeat(self) -> None:
        """No-op: an OS lock needs no renewal (kept so callers do not change)."""

    def release(self) -> None:
        if self._fd is None:                                  # we never held it: never touch someone else's lock
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    @staticmethod
    def is_free(path: Path) -> bool:
        """True if nobody currently holds the lock (used by tests and diagnostics)."""
        probe = RunLock(path)
        try:
            probe.acquire()
        except PipelineBusy:
            return False
        probe.release()
        return True

    def __enter__(self): self.acquire(); return self
    def __exit__(self, *exc): self.release()


def cleanup_tmp(layout: "Layout", older_than: float = 3600.0) -> int:
    """Delete orphaned *.tmp files (left when a process was killed mid-write). Only deletes files old enough."""
    n, now = 0, time.time()
    for p in layout.root.rglob("*.tmp"):
        try:
            if now - p.stat().st_mtime > older_than:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n
