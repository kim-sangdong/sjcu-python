"""유튜브 — YouTube Analytics API v2 reports.query.

- 동영상별: ids=channel==MINE, dimensions=video, 기간 합계(lifetime 아님). 쪽 나눔은 maxResults·startIndex.
- 트래픽 소스: dimensions=insightTrafficSourceType — 채널 전체 + 형식별(쇼츠·롱폼 영상 ID 로 filters).
- 수익 지표(estimatedRevenue 등)는 analytics.youtube_revenue=true 일 때만 요청한다.
  yt-analytics-monetary.readonly 범위가 필요하다(`python -m studio auth youtube --monetary`, YPP 가입 채널).
- 자격 증명은 studio.publish.google_auth.credentials() (함수 안에서 지연 import).
- 기간 경계 날짜는 KST 날짜로 보낸다. API 가 어느 시간대로 날짜를 자르는지는 미확인(태평양 시간으로 기억).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from studio.analytics.collect import CollectContext, content_row
from studio.analytics.common import HttpError, describe_request, num, request_json

# ---------------------------------------------------------------- 상수 (공식 문서 변경 시 여기만 고친다)

REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"     # [확인] Discovery 문서
IDS = "channel==MINE"
VIDEO_METRICS = ("views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
                 "subscribersGained", "subscribersLost", "likes", "comments", "shares")   # 뒤 6개는 [기억·미확인]
CORE_METRICS = ("views", "estimatedMinutesWatched", "subscribersGained")                # [확인] 공식 Metrics 페이지
REVENUE_METRICS = ("estimatedRevenue", "estimatedAdRevenue", "cpm")                     # [확인] monetary 범위 필요
TRAFFIC_DIMENSION = "insightTrafficSourceType"                                          # [확인] 공식 샘플
TRAFFIC_METRICS = ("views", "estimatedMinutesWatched")
MAX_RESULTS = 200          # dimensions=video 한 쪽 최대 행 수 [기억·미확인]
MAX_PAGES = 25
FILTER_MAX_IDS = 200       # filters=video==a,b,... 한 번에 넣는 영상 수 [기억·미확인: 상한 500]
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPE_MONETARY = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"

FIELD = {
    "views": "views",
    "estimatedMinutesWatched": "watch_minutes",
    "averageViewDuration": "avg_view_seconds",
    "averageViewPercentage": "avg_view_pct",
    "subscribersGained": "subs_gained",
    "subscribersLost": "subs_lost",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
}
REVENUE_FIELD = {"estimatedRevenue": "yt_revenue_usd", "estimatedAdRevenue": "yt_ad_revenue_usd", "cpm": "yt_cpm_usd"}

Fetch = Callable[[dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------- 자격 증명·호출

def _access_token(revenue: bool) -> str:
    from studio.publish import google_auth

    scopes = None
    if revenue:
        scopes = [*getattr(google_auth, "DEFAULT_SCOPES", (SCOPE_ANALYTICS,)), getattr(google_auth, "SCOPE_MONETARY", SCOPE_MONETARY)]
    try:
        creds = google_auth.credentials(scopes)
    except Exception as e:
        if revenue and "scope" in str(e).lower():
            raise RuntimeError(f"{e} — 수익 지표는 `python -m studio auth youtube --monetary` 로 받은 토큰이 필요합니다"
                               "(아니면 channel.yaml analytics.youtube_revenue: false).") from None
        raise
    return str(creds.token)


def _real_fetch(token: str) -> Fetch:
    def fetch(params: dict[str, Any]) -> dict[str, Any]:
        payload, _ = request_json("GET", REPORTS_URL, what="YouTube Analytics reports.query", params=params,
                                  headers={"Authorization": f"Bearer {token}"}, secrets=[token])
        return payload or {}
    return fetch


def _api_message(e: HttpError) -> str:
    p = e.payload
    if isinstance(p, dict) and isinstance(p.get("error"), dict):
        return str(p["error"].get("message") or "")[:300]
    return str(e)[:300]


def explain(e: HttpError) -> str:
    msg = _api_message(e)
    if e.status in (401, 403):
        return (f"YouTube Analytics 권한 오류(HTTP {e.status}: {msg}). refresh token 에 yt-analytics.readonly 범위가 있어야 합니다 — "
                "`python -m studio auth youtube` 로 다시 발급하고 GitHub Secret YOUTUBE_REFRESH_TOKEN 을 바꾸세요.")
    if e.status == 400:
        return f"YouTube Analytics 요청 값 오류(HTTP 400: {msg}) — studio/analytics/collect/youtube.py 상단의 지표·차원 상수를 확인하세요."
    return f"YouTube Analytics 호출 실패(HTTP {e.status or '네트워크'}: {msg}) — 잠시 뒤 다시 실행하세요."


def table_rows(table: dict[str, Any]) -> list[dict[str, Any]]:
    heads = [str(h.get("name")) for h in table.get("columnHeaders") or []]
    return [dict(zip(heads, r)) for r in table.get("rows") or []]


def _paged(fetch: Fetch, params: dict[str, Any], warnings: list[str]) -> list[dict[str, Any]]:
    tables, start = [], 1
    for _ in range(MAX_PAGES):
        t = fetch({**params, "startIndex": start})
        tables.append(t)
        n = len(t.get("rows") or [])
        if n < int(params.get("maxResults", MAX_RESULTS)):
            return tables
        start += n
    warnings.append(f"동영상이 {MAX_PAGES * MAX_RESULTS}개를 넘어 나머지는 받지 않았습니다(MAX_PAGES).")
    return tables


# ---------------------------------------------------------------- 수집

def collect(ctx: CollectContext) -> dict[str, Any]:
    base = {"ids": IDS, "startDate": ctx.period.start_date.isoformat(), "endDate": ctx.period.end_date.isoformat()}
    video_q = {**base, "dimensions": "video", "metrics": ",".join(VIDEO_METRICS), "sort": "-views", "maxResults": MAX_RESULTS}
    traffic_q = {**base, "dimensions": TRAFFIC_DIMENSION, "metrics": ",".join(TRAFFIC_METRICS)}
    revenue_q = ({**base, "dimensions": "video", "metrics": ",".join(REVENUE_METRICS), "sort": "-estimatedRevenue",
                  "maxResults": MAX_RESULTS} if ctx.settings.youtube_revenue else None)

    if ctx.dry_run:
        reqs = [describe_request("GET", REPORTS_URL, {**q, "startIndex": 1} if "maxResults" in q else q)
                for q in (video_q, traffic_q, revenue_q) if q]
        reqs.append(describe_request("GET", REPORTS_URL, {**traffic_q, "filters": "video==<쇼츠·롱폼 영상 ID 목록>"}))
        ctx.log.extend(f"[youtube] {r}" for r in reqs)
        return {"rows": [], "requests": reqs, "warnings": ["dry-run: 요청을 보내지 않았습니다."]}

    fetch: Fetch = ctx.fake.youtube if ctx.fake else _real_fetch(_access_token(ctx.settings.youtube_revenue))
    warnings: list[str] = []
    raw: dict[str, Any] = {}
    try:
        tables = _paged(fetch, video_q, warnings)
    except HttpError as e:
        if e.status != 400:
            raise RuntimeError(explain(e)) from None
        warnings.append(f"전체 지표 요청이 거절되어(HTTP 400: {_api_message(e)}) 기본 지표({', '.join(CORE_METRICS)})만 받았습니다 — "
                        "VIDEO_METRICS 상수를 확인하세요.")
        try:
            tables = _paged(fetch, {**video_q, "metrics": ",".join(CORE_METRICS)}, warnings)
        except HttpError as e2:
            raise RuntimeError(explain(e2)) from None
    raw["video"] = tables

    id_map = ctx.id_map
    rows: list[dict[str, Any]] = []
    for r in (x for t in tables for x in table_rows(t)):
        vid = str(r.get("video") or "")
        if not vid:
            continue
        vals = {FIELD[k]: num(v) for k, v in r.items() if k in FIELD}
        rows.append(content_row("youtube", vid, id_map.get(("youtube", vid)), lifetime=False, values=vals))

    if revenue_q:
        try:
            rev_tables = _paged(fetch, revenue_q, warnings)
            raw["revenue"] = rev_tables
            by_vid = {str(r.get("video")): r for t in rev_tables for r in table_rows(t)}
            for row in rows:
                rv = by_vid.get(row["remote_id"])
                if rv:
                    row["extra"] = {**row.get("extra", {}), **{REVENUE_FIELD[k]: num(v) for k, v in rv.items() if k in REVENUE_FIELD}}
        except HttpError as e:
            warnings.append(f"유튜브 수익 지표를 받지 못했습니다: {explain(e)}")

    traffic: list[dict[str, Any]] = []
    try:
        traffic += _traffic(fetch(traffic_q), "channel")
    except HttpError as e:
        warnings.append(f"트래픽 소스(채널 전체)를 받지 못했습니다: {explain(e)}")
    for kind in ("short", "longform"):
        ids = [r["remote_id"] for r in rows if r["kind"] == kind and (r["views"] or 0) > 0]
        acc: dict[str, dict[str, Any]] = {}
        try:
            for i in range(0, len(ids), FILTER_MAX_IDS):
                for t in _traffic(fetch({**traffic_q, "filters": "video==" + ",".join(ids[i:i + FILTER_MAX_IDS])}), kind):
                    a = acc.setdefault(t["source"], {"scope": kind, "source": t["source"], "views": 0.0, "watch_minutes": 0.0})
                    a["views"] += t["views"] or 0
                    a["watch_minutes"] += t["watch_minutes"] or 0
        except HttpError as e:
            warnings.append(f"트래픽 소스({kind})를 받지 못했습니다: {explain(e)}")
            continue
        traffic += sorted(acc.values(), key=lambda x: -x["views"])

    unmapped = [r for r in rows if not r["asset_id"]]
    if unmapped:
        warnings.append(f"queue.json 에 없는 영상 {len(unmapped)}개(조회 {sum(r['views'] or 0 for r in unmapped):,.0f}회)는 "
                        "콘텐츠별 표에서 빠지고 플랫폼 합계에만 들어갑니다.")
    return {"rows": rows, "traffic_sources": traffic, "raw": raw, "warnings": warnings}


def _traffic(table: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    out = []
    for r in table_rows(table):
        out.append({"scope": scope, "source": str(r.get(TRAFFIC_DIMENSION) or "UNKNOWN"),
                    "views": num(r.get("views")), "watch_minutes": num(r.get("estimatedMinutesWatched"))})
    return sorted(out, key=lambda x: -(x["views"] or 0))
