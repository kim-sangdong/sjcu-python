"""인스타그램 — 게시물 인사이트(GET /{ig-media-id}/insights). 릴스(쇼츠)·캐러셀(카드뉴스).

- queue.json 의 instagram 게시물(remote_id = 미디어 ID)만 조회한다. 값은 누적(lifetime)이다.
- 토큰: studio.publish.instagram.access_token() (회전 저장 포함) — 없으면 INSTAGRAM_TOKEN.
- 호스트: platforms.instagram.login 이 instagram 이면 graph.instagram.com, facebook 이면 graph.facebook.com.
"""

from __future__ import annotations

from typing import Any

from studio.analytics.collect import CollectContext, content_row
from studio.analytics.collect.graph import (
    FB_GRAPH,
    IG_GRAPH,
    GraphRateLimited,
    MediaFailed,
    fetch_insights,
    graph_url,
)
from studio.analytics.common import HttpError, describe_request, num, request_json
from studio.config import env

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다)

INSIGHTS_PATH = "/{media_id}/insights"
METRICS = {   # [확인-SDK] 이름은 SDK enum 에 있음. 어느 미디어 유형에 유효한지는 [기억·미확인]
    "short": ("views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "ig_reels_avg_watch_time"),
    "cardnews": ("views", "reach", "likes", "comments", "shares", "saved", "total_interactions"),
}
FIELD = {"views": "views", "reach": "reach", "likes": "likes", "comments": "comments", "shares": "shares", "saved": "saves"}
AVG_WATCH_METRIC = "ig_reels_avg_watch_time"     # 밀리초로 기억 [기억·미확인]
REAUTH = "instagram"


def _host(ctx: CollectContext) -> str:
    login = str((ctx.cfg.platforms.instagram.model_extra or {}).get("login", "instagram")).strip().lower()
    return FB_GRAPH if login == "facebook" else IG_GRAPH


def _token(ctx: CollectContext) -> str:
    login = "facebook" if _host(ctx) == FB_GRAPH else "instagram"
    try:
        from studio.publish.instagram import access_token
    except ImportError:  # pragma: no cover
        return str(env("INSTAGRAM_TOKEN"))
    return access_token(login, ctx.log)


def collect(ctx: CollectContext) -> dict[str, Any]:
    targets = ctx.media_targets("instagram")
    host = _host(ctx)
    if not targets:
        return {"rows": [], "warnings": ["조회할 인스타 게시물이 없습니다(queue.json 에 공개된 instagram 항목 없음)."]}
    if ctx.dry_run:
        reqs = [describe_request("GET", graph_url(host, INSIGHTS_PATH.format(media_id=t.remote_id)),
                                 {"metric": ",".join(METRICS.get(t.kind, METRICS["short"]))}) for t in targets]
        ctx.log.extend(f"[instagram] {r}" for r in reqs)
        return {"rows": [], "requests": reqs, "warnings": ["dry-run: 요청을 보내지 않았습니다."]}

    if ctx.fake:
        fetch = ctx.fake.instagram
    else:
        token = _token(ctx)

        def fetch(media_id: str, metrics: list[str]) -> dict[str, Any]:
            payload, _ = request_json("GET", graph_url(host, INSIGHTS_PATH.format(media_id=media_id)),
                                      what=f"인스타 인사이트 {media_id}", params={"metric": ",".join(metrics), "access_token": token},
                                      secrets=[token])
            return payload or {}

    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    warnings: list[str] = []
    skip: dict[str, set[str]] = {}
    failed, partial = 0, False
    for i, t in enumerate(targets):
        kind = t.kind if t.kind in METRICS else "short"
        try:
            vals = fetch_insights(fetch, t.remote_id, METRICS[kind], skip=skip.setdefault(kind, set()), reauth=REAUTH,
                                  warnings=warnings, label=f"인스타 {kind}")
        except GraphRateLimited as e:
            warnings.append(f"{e} — 게시물 {len(targets) - i}개를 받지 못했습니다.")
            partial = True
            break
        except (MediaFailed, HttpError) as e:
            failed += 1
            warnings.append(f"{t.asset_id}(미디어 {t.remote_id}) 조회 실패: {e}")
            continue
        raw[t.remote_id] = vals
        values = {FIELD[k]: num(v) for k, v in vals.items() if k in FIELD}
        if num(vals.get(AVG_WATCH_METRIC)) is not None:
            values["avg_view_seconds"] = num(vals[AVG_WATCH_METRIC]) / 1000
        rows.append(content_row("instagram", t.remote_id, t, lifetime=True, values=values,
                                extra={"interactions": num(vals.get("total_interactions"))}))
    if targets and failed == len(targets):
        raise RuntimeError(f"인스타 게시물 {failed}개 조회가 모두 실패했습니다. 첫 오류: {warnings[0] if warnings else '-'}")
    return {"rows": rows, "raw": raw, "warnings": warnings, "partial": partial or failed > 0}
