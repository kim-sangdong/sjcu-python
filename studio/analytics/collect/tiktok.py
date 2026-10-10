"""틱톡 — Display API POST /v2/video/query/ (조회·좋아요·댓글·공유 누적값). 스코프 video.list.

- queue.json 의 tiktok 게시물 중 remote_id 가 숫자(게시물 ID)인 것만 조회한다. 받은편지함 업로드(inbox)나
  처리 중에 기록된 publish_id(v_pub_… 등)는 게시물 ID 가 아니어서 조회할 수 없다 → 경고.
- 공개 영상만 돌려준다고 기억한다. 감사 전 SELF_ONLY 영상은 빠질 수 있다 → 경고.
- 토큰: studio.publish.tiktok.access_token() (refresh token 회전 저장 포함).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from studio.analytics.collect import CollectContext, content_row
from studio.analytics.common import HttpError, describe_request, num, request_json

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다) — [기억·미확인]

VIDEO_QUERY_URL = "https://open.tiktokapis.com/v2/video/query/"
FIELDS = ("id", "title", "create_time", "share_url", "duration", "view_count", "like_count", "comment_count", "share_count")
BATCH = 20                                   # filters.video_ids 최대 개수
POST_ID_RE = re.compile(r"^\d{8,}$")
FIELD = {"view_count": "views", "like_count": "likes", "comment_count": "comments", "share_count": "shares"}

Fetch = Callable[[list[str]], dict[str, Any]]


class _Api:
    def __init__(self, ctx: CollectContext):
        from studio.publish.tiktok import access_token

        self._access_token = access_token
        self.log = ctx.log
        self.token = access_token(ctx.log)
        self.refreshed = False

    def __call__(self, ids: list[str]) -> dict[str, Any]:
        try:
            return self._post(ids)
        except HttpError as e:
            if _code(e.payload) == "access_token_invalid" and not self.refreshed:
                self.refreshed = True
                self.token = self._access_token(self.log, force_refresh=True)
                return self._post(ids)
            raise

    def _post(self, ids: list[str]) -> dict[str, Any]:
        payload, _ = request_json("POST", VIDEO_QUERY_URL, what="틱톡 video/query", params={"fields": ",".join(FIELDS)},
                                  json_body={"filters": {"video_ids": ids}}, secrets=[self.token],
                                  headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=UTF-8"})
        payload = payload if isinstance(payload, dict) else {}
        code = _code(payload)
        if code not in ("", "ok"):
            raise HttpError(f"틱톡 video/query 실패({code}): {str((payload.get('error') or {}).get('message') or '')[:200]}",
                            status=200, payload=payload)
        return payload


def _code(payload: Any) -> str:
    err = payload.get("error") if isinstance(payload, dict) else None
    return str(err.get("code") or "") if isinstance(err, dict) else ""


def explain(e: HttpError) -> str:
    code = _code(e.payload)
    if code in ("access_token_invalid", "scope_not_authorized") or e.status in (401, 403):
        return (f"틱톡 인증 오류({code or e.status}). video.list 스코프로 `python -m studio auth tiktok` 을 다시 하고 "
                "TIKTOK_REFRESH_TOKEN 을 바꾸세요.")
    if code == "rate_limit_exceeded" or e.status == 429:
        return "틱톡 호출 한도에 걸렸습니다. 다음 실행에서 다시 받습니다."
    return str(e)[:300]


def collect(ctx: CollectContext) -> dict[str, Any]:
    targets = ctx.media_targets("tiktok")
    if not targets:
        return {"rows": [], "warnings": ["조회할 틱톡 게시물이 없습니다(queue.json 에 공개된 tiktok 항목 없음)."]}
    warnings: list[str] = []
    queryable = [t for t in targets if POST_ID_RE.match(t.remote_id)]
    skipped = [t for t in targets if not POST_ID_RE.match(t.remote_id)]
    if skipped:
        warnings.append(f"틱톡 {len(skipped)}개는 remote_id 가 게시물 ID 가 아니라(publish_id·받은편지함 업로드) 조회하지 못했습니다: "
                        + ", ".join(t.asset_id for t in skipped[:10]) + (" …" if len(skipped) > 10 else "")
                        + " — 앱에서 게시한 뒤 `publish mark` 로 게시물 ID 를 기록하면 다음 주부터 잡힙니다.")
    batches = [queryable[i:i + BATCH] for i in range(0, len(queryable), BATCH)]
    if ctx.dry_run:
        reqs = [describe_request("POST", VIDEO_QUERY_URL, {"fields": ",".join(FIELDS)},
                                 {"filters": {"video_ids": [t.remote_id for t in b]}}) for b in batches]
        ctx.log.extend(f"[tiktok] {r}" for r in reqs)
        return {"rows": [], "requests": reqs, "warnings": ["dry-run: 요청을 보내지 않았습니다.", *warnings]}
    if not queryable:
        return {"rows": [], "warnings": warnings, "partial": True}

    fetch: Fetch = ctx.fake.tiktok if ctx.fake else _Api(ctx)
    rows: list[dict[str, Any]] = []
    raw: list[Any] = []
    partial = bool(skipped)
    for b in batches:
        try:
            payload = fetch([t.remote_id for t in b])
        except HttpError as e:
            if not rows:
                raise RuntimeError(explain(e)) from None
            warnings.append(f"{explain(e)} — 게시물 {len(b)}개 이후를 받지 못했습니다.")
            partial = True
            break
        raw.append(payload)
        got = {str(v.get("id")): v for v in ((payload.get("data") or {}).get("videos") or []) if isinstance(v, dict)}
        for t in b:
            v = got.get(t.remote_id)
            if not v:
                warnings.append(f"{t.asset_id}(게시물 {t.remote_id}) 가 응답에 없습니다 — 비공개(SELF_ONLY)이거나 지워졌을 수 있습니다.")
                partial = True
                continue
            rows.append(content_row("tiktok", t.remote_id, t, lifetime=True,
                                    values={FIELD[k]: num(val) for k, val in v.items() if k in FIELD},
                                    extra={"share_url": v.get("share_url"), "duration": num(v.get("duration"))}))
    return {"rows": rows, "raw": raw, "warnings": warnings, "partial": partial}
