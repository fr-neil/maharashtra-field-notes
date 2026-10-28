#!/usr/bin/env python3
"""Write data/refresh-status.json from the refresh workflow's step outcomes.

Each FEED_<ID> environment variable holds a GitHub step outcome (success,
failure, cancelled or skipped). Feeds without one are checked snapshots rebuilt
by hand. "Last success" comes from each dataset's own timestamp, so a failed or
skipped run never claims fresher data than is published.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("HUB_PUBLIC_DIR") or APP / "public") / "data"

# feed id (as named in app/components/data-coverage.tsx) -> dataset file, timestamp field
FEEDS = {
    "rainfall": ("rainfall/index.json", "retrievedAt"),
    "season": ("rainfall-season/index.json", "retrievedAt"),
    "groundwater": ("groundwater/index.json", "builtAt"),
    "reservoirs": ("reservoirs-statewide/index.json", "retrievedAt"),
    "services": ("district-services/index.json", "retrievedAt"),
    "villages": ("villages-index.json", "builtAt"),
    "statistics": ("district-statistics/index.json", "retrievedAt"),
}
STATUS = {"success": "ok", "failure": "failed", "cancelled": "failed", "skipped": "not-due"}


def last_success(path: str, field: str) -> str | None:
    try:
        meta = json.loads((DATA / path).read_text(encoding="utf8")).get("meta", {})
    except (OSError, ValueError):
        return None
    value = meta.get(field)
    return value if isinstance(value, str) else None


def feed(feed_id: str, now: str) -> dict:
    outcome = os.environ.get(f"FEED_{feed_id.upper()}", "")
    status = STATUS.get(outcome, "snapshot")
    entry = {"id": feed_id, "status": status, "lastSuccessAt": last_success(*FEEDS[feed_id])}
    if status in ("ok", "failed"):
        entry["attemptedAt"] = now
    return entry


def main() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = {"updatedAt": now, "feeds": [feed(feed_id, now) for feed_id in FEEDS]}
    target = DATA / "refresh-status.json"
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    os.replace(temp, target)
    print(", ".join(f"{f['id']}: {f['status']}" for f in status["feeds"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
