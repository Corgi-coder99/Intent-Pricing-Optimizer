"""Per-user API accounts: each key has a name and a role; only the SHA-256 hash is stored (never the key itself).

Keys are random 256-bit strings, so plain SHA-256 is enough (no slow hash needed, unlike human-chosen passwords).
CLI:  python -m src.auth create --name alice --role user      (the key is shown only ONCE)
      python -m src.auth list | python -m src.auth disable --id <key_id>
"""
import argparse
import hashlib
import hmac
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from src.config import DATA_DIR
from src.pipeline.infra import atomic_write_json, read_json

KEY_FILE = DATA_DIR / "state" / "api_keys.json"
ROLES = {"user": 1, "admin": 2}
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,40}")


@dataclass(frozen=True)
class Principal:
    key_id: str
    name: str
    role: str


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _load(path: Path) -> Dict[str, dict]:
    return read_json(path, {})


def has_keys(path: Optional[Path] = None) -> bool:
    path = path or KEY_FILE
    return bool(_load(path))        # once the store has ever held a key (even disabled ones) it NEVER falls back to open mode


def create_key(name: str, role: str = "user", path: Optional[Path] = None) -> str:
    path = path or KEY_FILE
    if not _NAME.fullmatch(name or ""):
        raise ValueError("name must match [A-Za-z0-9_.-]{1,40}")
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    keys = _load(path)
    key_id = secrets.token_hex(4)
    while key_id in keys:
        key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    keys[key_id] = {"name": name, "role": role, "hash": _hash(secret), "disabled": False,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, keys)
    return f"{key_id}.{secret}"


def authenticate(token: str, path: Optional[Path] = None) -> Optional[Principal]:
    path = path or KEY_FILE
    key_id, _, secret = (token or "").partition(".")
    rec = _load(path).get(key_id)
    # always hash, so timing does not reveal whether the key_id exists
    candidate = _hash(secret)
    expected = rec["hash"] if rec else _hash("x")
    if rec and not rec.get("disabled") and hmac.compare_digest(candidate, expected):
        return Principal(key_id, rec["name"], rec["role"])
    return None


def disable_key(key_id: str, path: Optional[Path] = None) -> bool:
    path = path or KEY_FILE
    keys = _load(path)
    if key_id not in keys:
        return False
    keys[key_id]["disabled"] = True
    atomic_write_json(path, keys)
    return True


def list_keys(path: Optional[Path] = None) -> List[dict]:
    path = path or KEY_FILE
    return [{"id": k, **{f: v[f] for f in ("name", "role", "disabled", "created_at")}} for k, v in _load(path).items()]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.auth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--role", default="user", choices=sorted(ROLES))
    sub.add_parser("list")
    d = sub.add_parser("disable")
    d.add_argument("--id", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "create":
        print(f"API key (shown once, store it safely): {create_key(a.name, a.role)}")
    elif a.cmd == "list":
        for r in list_keys():
            print(r)
    else:
        print("disabled" if disable_key(a.id) else "not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
