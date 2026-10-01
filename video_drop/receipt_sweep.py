"""Read native receipts back on their own after a final tap.

A destination left "unconfirmed" by a final tap is checked on the phone at a few fixed
moments after that tap (RECHECKS), read-only, through scripts/phone_receipts.py. A
crosspost can sit "In progress" for minutes, so one look is not enough; four bounded looks
inside the first hour are. Each moment has a window: a moment the app missed (PC asleep,
phone busy) is skipped, never caught up in a burst. Only destinations whose receipt screens
were recorded (receipts.ROUTES status "verify") are ever looked at.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .receipts import ReceiptError, receipt_route

RECHECKS = (timedelta(minutes=3), timedelta(minutes=10), timedelta(minutes=25), timedelta(minutes=55))
LAST_WINDOW = timedelta(minutes=30)
ATTEMPTS_FILE = "receipt-attempts.json"


def unconfirmed(db) -> list[dict]:
    """Every unconfirmed destination with the time of its final tap (its latest 'unconfirmed' event)."""
    rows = db.execute("""
        SELECT d.release_id, d.platform, r.delivery_mode,
               (SELECT max(e.created_at) FROM event e WHERE e.release_id=d.release_id
                  AND e.platform=d.platform AND e.kind='unconfirmed')
        FROM destination d JOIN release r ON r.id=d.release_id
        WHERE d.status='unconfirmed' ORDER BY d.release_id, d.id""").fetchall()
    return [{"releaseId": row[0], "platform": row[1], "mode": row[2], "since": row[3]} for row in rows if row[3]]


def readable(platform: str, mode: str) -> bool:
    try:
        return receipt_route(platform, mode)["status"] == "verify"
    except ReceiptError:
        return False


def moment(since: datetime, now: datetime) -> int | None:
    """Which recheck is due now, or None outside every window."""
    for index, offset in enumerate(RECHECKS):
        start = since + offset
        end = since + RECHECKS[index + 1] if index + 1 < len(RECHECKS) else start + LAST_WINDOW
        if start <= now < end:
            return index
    return None


def due(waiting: list[dict], done: set[str], now: datetime) -> list[tuple[int, str, str]]:
    """(release id, platform, attempt key) for every check due now and not yet made."""
    found = []
    for item in waiting:
        if not readable(item["platform"], item["mode"]):
            continue
        since = datetime.fromisoformat(item["since"])
        if since.tzinfo is None:
            continue
        index = moment(since, now)
        key = f"{item['releaseId']}:{item['platform']}:{item['since']}:{index}"
        if index is not None and key not in done:
            found.append((item["releaseId"], item["platform"], key))
    return found


def load_done(state: Path) -> set[str]:
    try:
        return set(json.loads((state / ATTEMPTS_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def save_done(state: Path, done: set[str], now: datetime) -> None:
    """Keep only keys whose final tap is inside the last day."""
    cutoff = now - timedelta(days=1)
    kept = sorted(key for key in done if _since(key) is not None and _since(key) >= cutoff)
    (state / ATTEMPTS_FILE).write_text(json.dumps(kept), encoding="utf-8")


def _since(key: str) -> datetime | None:
    try:
        stamp = key.split(":", 2)[2].rsplit(":", 1)[0]
        return datetime.fromisoformat(stamp)
    except (IndexError, ValueError):
        return None
