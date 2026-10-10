"""댓글 파일 위치와 처리 장부(content/comments/replied.json).

장부 형식:
  {"version": 1, "updated_at": "...",
   "comments": {"<최상위 댓글 ID>": {"status": "replied"|"skipped", "at": "...", "thread_id": "...",
                                      "video_id": "...", "reply_id": "...", "reply": "...", "reason": "..."}}}
여러 프로세스가 동시에 써도 깨지지 않게 잠금 파일로 감싼다.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from studio import paths
from studio.models import read_json, write_json

LEDGER_FILE = "replied.json"


def comments_root() -> Path:
    return paths.CONTENT / "comments"


def day_dir(d: date | None = None) -> Path:
    return comments_root() / (d or paths.now_kst().date()).isoformat()


def ledger_path() -> Path:
    return comments_root() / LEDGER_FILE


def _empty() -> dict[str, Any]:
    return {"version": 1, "comments": {}}


def load_ledger() -> dict[str, Any]:
    data = read_json(ledger_path(), default=None)
    if not isinstance(data, dict) or not isinstance(data.get("comments"), dict):
        return _empty()
    return data


@contextmanager
def editing_ledger() -> Iterator[dict[str, Any]]:
    """with editing_ledger() as led: led['comments'][cid] = {...} — 잠근 상태에서 읽고 블록 끝에 저장."""
    root = comments_root()
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "replied.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            data = load_ledger()
            yield data
            data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            write_json(ledger_path(), data)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def record(comment_id: str, entry: dict[str, Any]) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    with editing_ledger() as led:
        led["comments"][comment_id] = {**led["comments"].get(comment_id, {}), **entry}


def handled_ids(ledger: dict[str, Any]) -> set[str]:
    """이미 처리한(답했거나 건너뛰기로 정한) 댓글·스레드 ID."""
    out: set[str] = set()
    for cid, e in ledger.get("comments", {}).items():
        out.add(cid)
        if isinstance(e, dict) and e.get("thread_id"):
            out.add(str(e["thread_id"]))
    return out
