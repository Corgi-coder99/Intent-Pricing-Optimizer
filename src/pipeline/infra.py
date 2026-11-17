"""Hạ tầng dùng chung: layout thư mục, ghi file nguyên tử, khoá chống chạy chồng."""
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
    def landing(self) -> Path: return self.root / "landing"        # nơi thả file nguồn (không sửa)
    @property
    def bronze(self) -> Path: return self.root / "bronze"          # bản sao thô, bất biến, đặt tên theo hash
    @property
    def silver(self) -> Path: return self.root / "silver"          # đã ép kiểu, kiểm tra, khử trùng
    @property
    def gold(self) -> Path: return self.root / "gold"              # bảng sẵn sàng cho mô hình và dashboard
    @property
    def quarantine(self) -> Path: return self.root / "quarantine"  # dòng bị loại kèm lý do
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
    """Dấu vân tay của tập file đầu vào; đổi nội dung thì đổi dấu vân tay."""
    h = hashlib.sha256()
    for p in sorted(Path(x) for x in paths):
        h.update(p.name.encode())
        h.update(sha256_file(p).encode() if p.exists() else b"missing")
    return h.hexdigest()


class PipelineBusy(RuntimeError):
    pass


class RunLock:
    """Khoá toàn pipeline: chỉ một lần chạy tại một thời điểm (tạo file bằng O_EXCL, thao tác nguyên tử).

    Chủ khoá gọi heartbeat() định kỳ; khoá không được gia hạn quá `ttl` giây bị coi là của tiến trình
    đã chết và được thu hồi. Không dùng kiểm tra PID vì trên Windows os.kill(pid, 0) có thể kết thúc tiến trình.
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
                    self.path.unlink(missing_ok=True)   # khoá chết -> thu hồi, thử lại
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise PipelineBusy(f"Pipeline is already running (lock: {self.path})")
            time.sleep(0.2)

    def heartbeat(self) -> None:
        """Gia hạn khoá: chủ khoá còn sống thì mtime luôn mới, nên TTL chỉ để thu hồi khoá của tiến trình đã chết."""
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
        if info.get("token") == self.token:   # chỉ xoá khoá của chính mình
            self.path.unlink(missing_ok=True)

    def __enter__(self): self.acquire(); return self
    def __exit__(self, *exc): self.release()


def cleanup_tmp(layout: "Layout", older_than: float = 3600.0) -> int:
    """Xoá file *.tmp mồ côi (do tiến trình bị kill giữa lúc ghi). Chỉ xoá file đủ cũ."""
    n, now = 0, time.time()
    for p in layout.root.rglob("*.tmp"):
        try:
            if now - p.stat().st_mtime > older_than:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n
