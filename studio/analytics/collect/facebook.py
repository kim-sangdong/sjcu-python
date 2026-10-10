"""페이스북 페이지 릴스 — GET /{video-id}/video_insights?metric=...&period=lifetime.

- queue.json 의 facebook 게시물(remote_id = video_id)만 조회한다. 값은 누적(lifetime)이다.
- 토큰: FACEBOOK_PAGE_TOKEN → token_store("facebook").page_token.
- 지표 이름은 모두 [기억·미확인]. 거절되는 지표는 경고로 남기고 빼고 받는다(graph.fetch_insights).
"""

from __future__ import annotations

from typing import Any

from studio.analytics.collect import CollectContext, content_row
from studio.analytics.collect.graph import FB_GRAPH, GraphRateLimited, MediaFailed, fetch_insights, graph_url
from studio.analytics.common import HttpError, describe_request, num, request_json
from studio.config import MissingSecret, env

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다) — [기억·미확인]

INSIGHTS_PATH = "/{video_id}/video_insights"
PERIOD = "lifetime"
METRICS = ("blue_reels_play_count", "post_impressions_unique", "post_video_avg_time_watched", "post_video_social_actions")
FIELD = {"blue_reels_play_count": "views", "post_impressions_unique": "reach"}
AVG_WATCH_METRIC = "post_video_avg_time_watched"    # 밀리초로 기억
SOCIAL_METRIC = "post_video_social_actions"         # {"LIKE"|"REACTION": n, "COMMENT": n, "SHARE": n} 형태로 기억
REAUTH = "facebook"


def _token() -> str:
    tok = env("FACEBOOK_PAGE_TOKEN", required=False)
    if not tok:
        from studio.tokens import token_store

        tok = (token_store().get("facebook") or {}).get("page_token")
    if not tok:
        raise MissingSecret("환경변수 FACEBOOK_PAGE_TOKEN 이 없습니다. `python -m studio auth facebook` 출력값을 .env 또는 GitHub Secrets 에 넣어 주세요.")
    return str(tok)


def social_counts(value: Any) -> dict[str, float | None]:
    """post_video_social_actions 값 → likes·comments·shares. 형태를 모르면 빈 dict."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, float | None] = {}
    for k, v in value.items():
        key = str(k).lower()
        n = num(v)
        if n is None:
            continue
        if "share" in key:
            out["shares"] = (out.get("shares") or 0) + n
        elif "comment" in key:
            out["comments"] = (out.get("comments") or 0) + n
        elif "like" in key or "reaction" in key:
            out["likes"] = (out.get("likes") or 0) + n
    return out


def collect(ctx: CollectContext) -> dict[str, Any]:
    targets = ctx.media_targets("facebook")
    if not targets:
        return {"rows": [], "warnings": ["조회할 페이스북 릴스가 없습니다(queue.json 에 공개된 facebook 항목 없음)."]}
    if ctx.dry_run:
        reqs = [describe_request("GET", graph_url(FB_GRAPH, INSIGHTS_PATH.format(video_id=t.remote_id)),
                                 {"metric": ",".join(METRICS), "period": PERIOD}) for t in targets]
        ctx.log.extend(f"[facebook] {r}" for r in reqs)
        return {"rows": [], "requests": reqs, "warnings": ["dry-run: 요청을 보내지 않았습니다."]}

    if ctx.fake:
        fetch = ctx.fake.facebook
    else:
        token = _token()

        def fetch(video_id: str, metrics: list[str]) -> dict[str, Any]:
            payload, _ = request_json("GET", graph_url(FB_GRAPH, INSIGHTS_PATH.format(video_id=video_id)),
                                      what=f"페이스북 릴스 인사이트 {video_id}",
                                      params={"metric": ",".join(metrics), "period": PERIOD, "access_token": token}, secrets=[token])
            return payload or {}

    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    warnings: list[str] = []
    skip: set[str] = set()
    failed, partial = 0, False
    for i, t in enumerate(targets):
        try:
            vals = fetch_insights(fetch, t.remote_id, METRICS, skip=skip, reauth=REAUTH, warnings=warnings, label="페이스북 릴스")
        except GraphRateLimited as e:
            warnings.append(f"{e} — 릴스 {len(targets) - i}개를 받지 못했습니다.")
            partial = True
            break
        except (MediaFailed, HttpError) as e:
            failed += 1
            warnings.append(f"{t.asset_id}(영상 {t.remote_id}) 조회 실패: {e}")
            continue
        raw[t.remote_id] = vals
        values: dict[str, Any] = {FIELD[k]: num(v) for k, v in vals.items() if k in FIELD}
        values.update(social_counts(vals.get(SOCIAL_METRIC)))
        if num(vals.get(AVG_WATCH_METRIC)) is not None:
            values["avg_view_seconds"] = num(vals[AVG_WATCH_METRIC]) / 1000
        rows.append(content_row("facebook", t.remote_id, t, lifetime=True, values=values))
    if targets and failed == len(targets):
        raise RuntimeError(f"페이스북 릴스 {failed}개 조회가 모두 실패했습니다. 첫 오류: {warnings[0] if warnings else '-'}")
    return {"rows": rows, "raw": raw, "warnings": warnings, "partial": partial or failed > 0}
