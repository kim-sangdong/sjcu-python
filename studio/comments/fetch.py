"""새 댓글 모으기 → content/comments/<날짜>/inbox.json.

commentThreads.list(allThreadsRelatedToChannelId, order=time, moderationStatus=published) 를 페이지 순회한다.
빼는 것: 기간 밖, 채널 본인 댓글, 채널이 이미 답한 스레드, 장부(replied.json)에 있는 스레드, 답글을 달 수 없는 스레드.
할당량: 페이지당 1 unit, 영상 제목 50개당 1 unit, 답글 확인 1 unit/스레드(답글이 많을 때만).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from studio import paths
from studio.comments import filters, store
from studio.config import Config, load_env
from studio.models import read_json, write_json
from studio.publish import google_auth

PAGE_SIZE = 100                 # commentThreads.list maxResults 상한
MAX_PAGES = 10
MAX_REPLY_LOOKUPS = 50          # 답글이 많은 스레드의 채널 답글 확인(comments.list) 상한
HELD_MAX_PAGES = 3
EXECUTE_RETRIES = 3
THREAD_PARTS = "snippet,replies"


@dataclass
class FetchReport:
    out_dir: Path
    items: list[dict[str, Any]] = field(default_factory=list)
    channel_id: str = ""
    scanned: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    held_for_review: int | None = None
    pages: int = 0

    def bump(self, key: str) -> None:
        self.excluded[key] = self.excluded.get(key, 0) + 1


def _published(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _author_id(snippet: dict[str, Any]) -> str:
    a = snippet.get("authorChannelId")
    return str(a.get("value", "")) if isinstance(a, dict) else str(a or "")


def _text(snippet: dict[str, Any]) -> str:
    # textOriginal 은 작성자 본인에게만 보장된다 → plainText 형식의 textDisplay 를 먼저 쓴다.
    return str(snippet.get("textDisplay") or snippet.get("textOriginal") or "")


def channel_id_of(yt) -> str:
    load_env()
    cid = os.environ.get(google_auth.ENV_CHANNEL_ID, "").strip()
    if cid:
        return cid
    items = yt.channels().list(part="id", mine=True).execute(num_retries=EXECUTE_RETRIES).get("items") or []
    if not items:
        raise google_auth.GoogleAuthError("이 토큰의 계정에 유튜브 채널이 없습니다. 채널 소유 계정으로 `python -m studio auth youtube` 를 다시 하세요.")
    return items[0]["id"]


def asset_ids_by_video() -> dict[str, str]:
    """발행 대기열(content/weeks/*/queue.json)에서 유튜브 영상 ID → 콘텐츠 ID. 답글 쓸 때 원고를 찾는 데 쓴다."""
    out: dict[str, str] = {}
    for q in sorted((paths.CONTENT / "weeks").glob("*/queue.json")):
        data = read_json(q, default={}) or {}
        for t in data.get("targets", []) if isinstance(data, dict) else []:
            if isinstance(t, dict) and t.get("platform") == "youtube" and t.get("remote_id"):
                out[str(t["remote_id"])] = str(t.get("asset_id", ""))
    return out


def _channel_replied(yt, top_id: str, channel_id: str) -> bool:
    resp = yt.comments().list(part="snippet", parentId=top_id, maxResults=100, textFormat="plainText").execute(
        num_retries=EXECUTE_RETRIES
    )
    return any(_author_id(c.get("snippet", {})) == channel_id for c in resp.get("items") or [])


def _video_titles(yt, ids: list[str]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        resp = yt.videos().list(part="snippet", id=",".join(chunk)).execute(num_retries=EXECUTE_RETRIES)  # id 와 maxResults 는 함께 못 씀
        for v in resp.get("items") or []:
            titles[v["id"]] = v.get("snippet", {}).get("title", "")
    return titles


def _held_count(yt, channel_id: str) -> int:
    n, token = 0, None
    for _ in range(HELD_MAX_PAGES):
        kw: dict[str, Any] = {"part": "id", "allThreadsRelatedToChannelId": channel_id,
                              "moderationStatus": "heldForReview", "maxResults": PAGE_SIZE}
        if token:
            kw["pageToken"] = token
        resp = yt.commentThreads().list(**kw).execute(num_retries=EXECUTE_RETRIES)
        n += len(resp.get("items") or [])
        token = resp.get("nextPageToken")
        if not token:
            break
    return n


def fetch_inbox(cfg: Config, *, since_hours: float = 48, max_items: int = 200, yt=None,
                now: datetime | None = None, out_dir: Path | None = None) -> FetchReport:
    """새 댓글을 모아 inbox.json·summary.json 을 쓴다. 할당량·권한 오류는 HttpError 그대로 올린다."""
    yt = yt or google_auth.youtube_service()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=since_hours)
    rep = FetchReport(out_dir=out_dir or store.day_dir(now.astimezone(paths.KST).date()))
    rep.channel_id = channel_id_of(yt)
    handled = store.handled_ids(store.load_ledger())

    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []   # (thread, top snippet)
    token = None
    while rep.pages < MAX_PAGES and len(candidates) < max_items:
        kw: dict[str, Any] = {
            "part": THREAD_PARTS, "allThreadsRelatedToChannelId": rep.channel_id, "order": "time",
            "moderationStatus": "published", "textFormat": "plainText", "maxResults": PAGE_SIZE,
        }
        if token:
            kw["pageToken"] = token
        resp = yt.commentThreads().list(**kw).execute(num_retries=EXECUTE_RETRIES)
        rep.pages += 1
        threads = resp.get("items") or []
        any_recent = False
        for th in threads:
            rep.scanned += 1
            sn = th.get("snippet", {})
            top = sn.get("topLevelComment", {})
            ts = top.get("snippet", {})
            at = _published(ts.get("publishedAt"))
            if at is None or at < cutoff:
                rep.bump("기간 밖")
                continue
            any_recent = True
            if _author_id(ts) == rep.channel_id:
                rep.bump("채널 본인 댓글")
                continue
            if th.get("id") in handled or top.get("id") in handled:
                rep.bump("이미 처리함(장부)")
                continue
            if sn.get("canReply") is False:
                rep.bump("답글 불가")
                continue
            replies = (th.get("replies") or {}).get("comments") or []
            if any(_author_id(r.get("snippet", {})) == rep.channel_id for r in replies):
                rep.bump("채널이 이미 답함")
                continue
            candidates.append((th, ts))
            if len(candidates) >= max_items:
                break
        token = resp.get("nextPageToken")
        if not token or not any_recent:   # order=time 이라 기간 밖만 나온 페이지 뒤는 더 오래된 댓글
            break

    lookups = 0
    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for th, ts in candidates:
        sn = th.get("snippet", {})
        shown = len((th.get("replies") or {}).get("comments") or [])
        if int(sn.get("totalReplyCount") or 0) > shown and lookups < MAX_REPLY_LOOKUPS:
            lookups += 1
            top_id = sn.get("topLevelComment", {}).get("id") or th.get("id")
            if _channel_replied(yt, top_id, rep.channel_id):
                rep.bump("채널이 이미 답함")
                continue
        kept.append((th, ts))

    video_ids = sorted({str(th.get("snippet", {}).get("videoId") or ts.get("videoId") or "") for th, ts in kept} - {""})
    titles = _video_titles(yt, video_ids) if video_ids else {}
    assets = asset_ids_by_video()
    for th, ts in kept:
        sn = th.get("snippet", {})
        vid = str(sn.get("videoId") or ts.get("videoId") or "")
        text = _text(ts)
        rep.items.append({
            "thread_id": th.get("id"),
            "comment_id": sn.get("topLevelComment", {}).get("id") or th.get("id"),
            "video_id": vid,
            "video_title": titles.get(vid, ""),
            "asset_id": assets.get(vid),
            "author": ts.get("authorDisplayName", ""),
            "text": text,
            "published_at": ts.get("publishedAt"),
            "like_count": int(ts.get("likeCount") or 0),
            "reply_count": int(sn.get("totalReplyCount") or 0),
            "flags": filters.comment_flags(text, cfg),
        })
    try:
        rep.held_for_review = _held_count(yt, rep.channel_id)
    except Exception:  # 요약용이라 실패해도 수집 결과는 쓴다
        rep.held_for_review = None

    write_json(rep.out_dir / "inbox.json", rep.items)
    write_json(rep.out_dir / "summary.json", {
        "fetched_at": now.isoformat(timespec="seconds"),
        "since_hours": since_hours,
        "channel_id": rep.channel_id,
        "pages": rep.pages,
        "scanned": rep.scanned,
        "new": len(rep.items),
        "spam_flagged": sum(1 for i in rep.items if "spam" in i["flags"]),
        "questions": sum(1 for i in rep.items if "question" in i["flags"]),
        "excluded": rep.excluded,
        "held_for_review": rep.held_for_review,
    })
    return rep
