"""답글 올리기 — replies.json [{comment_id, reply, skip?, reason?}] → comments.insert(snippet.parentId, textOriginal).

안전장치(하나라도 걸리면 그 항목은 올리지 않고 'rejected'):
  금지 표현(rules.banned_phrases), 외부 링크(cfg.app.link_base 호스트만 허용), 500자 초과,
  같은 문구 3번째 사용(이번 파일 + 최근 30일 장부), 빈 답글.
건너뜀: skip=true(장부에 'skipped' 로 기록), 이미 답한 comment_id, 같은 파일 안 중복.
실행당 최대 건수(--max)를 넘는 항목은 'deferred' — 장부에 남기지 않으므로 다음 실행에서 다시 올라간다.
올린 즉시 장부(content/comments/replied.json)에 기록해 중간에 멈춰도 중복 답글이 생기지 않는다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from studio.comments import filters, store
from studio.config import Config
from studio.models import read_json, write_json
from studio.publish import google_auth

POST_INTERVAL_SECONDS = 3.0    # 연달아 올릴 때 간격(스팸 판정 회피)
EXECUTE_RETRIES = 3
DRYRUN_PREFIX = "dryrun-"

_sleep: Callable[[float], None] = time.sleep


class RepliesFileError(ValueError):
    """replies.json 자체를 읽을 수 없을 때."""


@dataclass
class PostReport:
    results: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
    stopped: str = ""          # 할당량·인증 문제로 중간에 멈춘 이유

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r["status"] == status)

    @property
    def ok(self) -> bool:
        return not (self.count("rejected") or self.count("failed"))


def load_replies(path: Path) -> list[Any]:
    try:
        data = read_json(path, default=None)
    except ValueError as e:
        raise RepliesFileError(f"{path}: JSON 형식 오류 — {e}") from None
    if data is None:
        raise RepliesFileError(f"{path}: 파일이 없습니다.")
    if not isinstance(data, list):
        raise RepliesFileError(f"{path}: 최상위가 목록([...])이어야 합니다.")
    return data


def _recent_reply_counts(ledger: dict[str, Any], now: datetime) -> dict[str, int]:
    since = now - timedelta(days=filters.SAME_REPLY_WINDOW_DAYS)
    counts: dict[str, int] = {}
    for e in ledger.get("comments", {}).values():
        if not isinstance(e, dict) or e.get("status") != "replied" or not e.get("reply"):
            continue
        try:
            at = datetime.fromisoformat(str(e.get("at")))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at >= since:
            k = filters.reply_key(e["reply"])
            counts[k] = counts.get(k, 0) + 1
    return counts


def post_replies(path: Path, cfg: Config, *, dry_run: bool = False, max_posts: int = 30, yt=None,
                 now: datetime | None = None) -> PostReport:
    from googleapiclient.errors import HttpError

    from studio.publish.youtube import QUOTA_REASONS, transient_errors

    transient = transient_errors()
    items = load_replies(path)
    now = now or datetime.now(timezone.utc)
    rep = PostReport(dry_run=dry_run)
    ledger = store.load_ledger()
    done = ledger.get("comments", {})
    counts = _recent_reply_counts(ledger, now)
    inbox = {str(i.get("comment_id")): i for i in (read_json(path.parent / "inbox.json", default=[]) or []) if isinstance(i, dict)}
    max_chars = int(getattr(cfg.platforms.youtube, "comment_reply_max_chars", filters.MAX_REPLY_CHARS))
    seen: set[str] = set()
    posted = 0

    for n, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not str(item.get("comment_id") or "").strip():
            rep.results.append({"index": n, "comment_id": None, "status": "rejected", "reasons": ["comment_id 가 없습니다"]})
            continue
        cid = str(item["comment_id"]).strip()
        res: dict[str, Any] = {"index": n, "comment_id": cid}
        rep.results.append(res)
        meta = inbox.get(cid, {})
        if cid in seen:
            res.update(status="duplicate", reasons=["같은 파일에 같은 comment_id 가 또 있습니다"])
            continue
        seen.add(cid)
        if (done.get(cid) or {}).get("status") == "replied":
            res.update(status="already", reasons=["이미 답한 댓글입니다(장부)"])
            continue
        if item.get("skip"):
            res.update(status="skipped", reasons=[str(item.get("reason") or "")])
            if not dry_run:
                store.record(cid, {"status": "skipped", "reason": str(item.get("reason") or ""),
                                   "thread_id": meta.get("thread_id"), "video_id": meta.get("video_id")})
            continue
        reply = str(item.get("reply") or "")
        problems = filters.reply_problems(reply, cfg, max_chars)
        key = filters.reply_key(reply)
        if key and counts.get(key, 0) >= filters.MAX_SAME_REPLY:
            problems.append(f"같은 문구를 최근 {filters.SAME_REPLY_WINDOW_DAYS}일 안에 {counts[key]}번 썼습니다 — 댓글마다 다르게 쓰세요")
        if inbox and cid not in inbox:
            res["warnings"] = ["inbox.json 에 없는 comment_id 입니다 — ID 를 확인하세요"]
        if problems:
            res.update(status="rejected", reasons=problems)
            continue
        if rep.stopped or posted >= max_posts:
            res.update(status="deferred", reasons=[rep.stopped or f"실행당 최대 {max_posts}건 — 다음 실행에서 올립니다"])
            continue

        if dry_run:
            res.update(status="posted", reply_id=f"{DRYRUN_PREFIX}{n}", reply=reply)
        else:
            if yt is None:
                yt = google_auth.youtube_service()
            if posted:
                _sleep(POST_INTERVAL_SECONDS)
            try:
                resp = yt.comments().insert(
                    part="snippet", body={"snippet": {"parentId": cid, "textOriginal": reply}}
                ).execute(num_retries=EXECUTE_RETRIES)
            except HttpError as e:
                info = google_auth.api_error(e)
                why = f"HTTP {info.status} {info.reason}: {info.message}"
                if info.reason in QUOTA_REASONS or info.status == 429:
                    rep.stopped = "API 할당량 초과 — 한국 16~17시(태평양 자정) 이후 다시 실행하세요"
                    res.update(status="deferred", reasons=[rep.stopped])
                    continue
                if info.status == 401:
                    rep.stopped = "인증 실패 — " + google_auth.REAUTH_HINT
                    res.update(status="deferred", reasons=[rep.stopped])
                    continue
                hint = {403: " — 댓글이 막혔거나(작성자 차단·댓글 사용 중지) 권한(youtube.force-ssl)이 없습니다",
                        404: " — 댓글이 삭제됐습니다"}.get(info.status, "")
                res.update(status="failed", reasons=[why + hint])
                continue
            except transient as e:
                res.update(status="failed", reasons=[f"네트워크 오류({type(e).__name__}) — 다음 실행에서 다시 올립니다"])
                continue
            reply_id = resp.get("id")
            res.update(status="posted", reply_id=reply_id, reply=reply)
            store.record(cid, {"status": "replied", "reply_id": reply_id, "reply": reply,
                               "thread_id": meta.get("thread_id"), "video_id": meta.get("video_id")})
        posted += 1
        if key:
            counts[key] = counts.get(key, 0) + 1

    out = path.with_name(path.stem + (".dryrun" if dry_run else "") + ".result.json")
    write_json(out, {
        "file": path.name,
        "dry_run": dry_run,
        "at": now.isoformat(timespec="seconds"),
        "summary": {s: rep.count(s) for s in ("posted", "skipped", "already", "duplicate", "rejected", "deferred", "failed")},
        "stopped": rep.stopped,
        "results": rep.results,
    })
    return rep
