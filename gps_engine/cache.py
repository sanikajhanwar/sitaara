"""
GPS Engine — Result Cache
=========================
File-based cache of pipeline results, keyed by the parcel identity.
BRD 6.2: "Store cached portal results (max 24-hour TTL) to reduce load and avoid
repeated scraping."

Only SUCCESS / REFER results are cached — FAILED (portal down, timeout) is retried
next run so a transient outage does not poison the cache.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_TTL_SECONDS = 24 * 3600


def _key(state: str, district: str, tehsil: str, village: str, khasra: str, extra: Optional[Dict] = None) -> str:
    parts = [state, district, tehsil, village, khasra]
    if extra:
        parts += [f"{k}={extra[k]}" for k in sorted(extra) if extra[k]]
    raw = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load(cache_dir: Path, state, district, tehsil, village, khasra, extra=None,
         ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Optional[Dict[str, Any]]:
    f = cache_dir / f"{_key(state, district, tehsil, village, khasra, extra)}.json"
    if not f.exists():
        return None
    try:
        blob = json.loads(f.read_text())
    except Exception:
        return None
    age = time.time() - blob.get("_cached_at", 0)
    if age > ttl_seconds:
        return None
    result = blob.get("result")
    if result is not None:
        result.setdefault("warnings", []).append(
            f"served from cache ({int(age // 60)} min old; TTL {ttl_seconds // 3600} h)"
        )
        result["from_cache"] = True
    return result


def store(cache_dir: Path, result: Dict[str, Any], state, district, tehsil, village, khasra, extra=None) -> None:
    if result.get("status") not in ("SUCCESS", "REFER"):
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{_key(state, district, tehsil, village, khasra, extra)}.json"
    f.write_text(json.dumps({"_cached_at": time.time(), "result": result}, ensure_ascii=False, indent=2))
