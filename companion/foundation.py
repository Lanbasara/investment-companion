from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


class CompanionError(RuntimeError):
    """Stable application error shared by domain, platform, and interfaces."""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(*parts: Any) -> str:
    payload = "\x1f".join(
        canonical(part) if not isinstance(part, str) else part for part in parts
    )
    return hashlib.sha256(payload.encode()).hexdigest()
