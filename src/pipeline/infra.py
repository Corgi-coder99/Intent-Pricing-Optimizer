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
    """Pipeline-wide lock: only one run at a time (file created with O_EXCL, an atomic operation).

    The lock owner calls heartbeat() periodically; a lock not renewed for more than `ttl` seconds is treated as belonging to a
    dead process and is reclaimed. No PID check, because on Windows os.kill(pid, 0) can terminate the process.
    """

    def __init__(self, path: Path, ttl: int = 600, wait: float = 0.0):
        self.path, self.ttl, self.wait = path, ttl, wait
        self.token = uuid.uuid4().hex

    def _try_create(self) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "token": self.token, "ts": time.time()}, f)
        return True

    def acquire(self) -> None:
        deadline = time.monotonic() + self.wait
        while True:
            if self._try_create():
                return
            try:
                age = time.time() - self.path.stat().st_mtime
                if age > self.ttl:
                    self.path.unlink(missing_ok=True)   # dead lock -> reclaim, retry
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise PipelineBusy(f"Pipeline is already running (lock: {self.path})")
            time.sleep(0.2)

    def heartbeat(self) -> None:
        """Renew the lock: while the owner is alive the mtime stays fresh, so the TTL only reclaims locks of dead processes."""
        try:
            os.utime(self.path)
        except FileNotFoundError:
            pass

    def release(self) -> None:
        info: Optional[dict] = None
        try:
            info = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return
        if info.get("token") == self.token:   # only remove our own lock
            self.path.unlink(missing_ok=True)

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
