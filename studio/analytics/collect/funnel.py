"""클릭·설치·결제 — Supabase PostgREST 로 link_clicks, app_installs, purchases 를 읽는다.

읽는 범위(귀속 계산에 필요한 만큼):
- purchases:  created_at ∈ [기간 시작, 기간 끝 + PURCHASE_LAG) — 귀속 단계가 event_timestamp 로 기간 안만 고른다.
- app_installs: 기간 안 설치 + 기간 안에 결제한 사용자의 (예전) 설치 기록.
- link_clicks: [기간 시작 − 귀속 창, 기간 끝) + 예전 설치의 귀속 창 확인에 필요한 클릭(그 콘텐츠·그 시점만).
페이지 나눔은 Range 헤더(Range-Unit: items) + Content-Range 응답. app_user_id 는 기본으로 해시해서 저장한다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from studio.analytics.collect import CollectContext
from studio.analytics.common import HttpError, describe_request, parse_time, request_json
from studio.config import env

# ---------------------------------------------------------------- 상수

REST_PATH = "/rest/v1/{table}"
PAGE_SIZE = 1000                     # Supabase 기본 max-rows 와 같게
MAX_PAGES = 200
IN_CHUNK = 80                        # in.(...) 한 번에 넣는 값 수(주소 길이 제한 대비)
PURCHASE_LAG = timedelta(days=2)     # 웹훅이 늦게 도착하는 경우 대비
CLICK_COLUMNS = "id,created_at,source,medium,campaign,content,os"
INSTALL_COLUMNS = "id,created_at,app_user_id,os,utm_source,utm_medium,utm_campaign,utm_content,app_version"
PURCHASE_COLUMNS = ("id,created_at,event_id,event_type,app_user_id,product_id,period_type,price,currency,price_in_usd,"
                    "store,environment,event_timestamp,"
                    "is_trial_conversion:raw->>is_trial_conversion,cancel_reason:raw->>cancel_reason")
_CONTENT_RANGE = re.compile(r"^\s*(?:items\s+)?(\d+)-(\d+)/(\d+|\*)\s*$")

Getter = Callable[[str, str, list[tuple[str, str]]], list[dict[str, Any]]]


def hash_uid(uid: Any) -> str | None:
    """app_user_id → 짧은 해시(보고서 파일에 원래 ID 를 남기지 않는다). 같은 ID 는 같은 해시라 조인은 그대로 된다."""
    if uid is None or uid == "":
        return None
    return "u_" + hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:16]


def pg_in(values: list[str]) -> str:
    """PostgREST in.(...) — 값마다 큰따옴표로 감싸 쉼표·콜론·괄호가 있어도 안전하게."""
    quoted = ['"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values]
    return f"in.({','.join(quoted)})"


def ts(t: datetime) -> str:
    return t.isoformat()


class _Rest:
    def __init__(self) -> None:
        self.url = str(env("SUPABASE_URL")).rstrip("/")
        self.key = str(env("SUPABASE_SERVICE_ROLE_KEY"))

    def get(self, table: str, select: str, filters: list[tuple[str, str]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            params = [("select", select), *filters, ("order", "id.asc")]
            headers = {"apikey": self.key, "Accept": "application/json",
                       "Range-Unit": "items", "Range": f"{offset}-{offset + PAGE_SIZE - 1}", "Prefer": "count=exact"}
            if self.key.startswith("eyJ"):   # 옛 JWT 형식 키만 Bearer 로(새 sb_secret_ 키는 apikey 헤더만)
                headers["Authorization"] = f"Bearer {self.key}"
            try:
                payload, r = request_json("GET", self.url + REST_PATH.format(table=table), what=f"Supabase {table} 읽기",
                                          params=params, headers=headers, secrets=[self.key], ok_status=(416,))
            except HttpError as e:
                raise RuntimeError(explain(e, table)) from None
            if r.status_code == 416:     # 범위를 넘음 = 끝
                return rows
            page = payload if isinstance(payload, list) else []
            rows.extend(page)
            m = _CONTENT_RANGE.match(r.headers.get("Content-Range", ""))
            if m and m.group(3) != "*":
                end, total = int(m.group(2)), int(m.group(3))
                if end + 1 >= total or not page:
                    return rows
                offset = end + 1
            else:
                if len(page) < PAGE_SIZE:
                    return rows
                offset += len(page)
        raise RuntimeError(f"Supabase {table}: {MAX_PAGES * PAGE_SIZE}행을 넘어 읽기를 멈췄습니다(MAX_PAGES 를 늘리세요).")


def explain(e: HttpError, table: str) -> str:
    p = e.payload if isinstance(e.payload, dict) else {}
    msg = str(p.get("message") or p.get("hint") or e)[:300]
    if e.status in (401, 403):
        return f"Supabase 인증 실패(HTTP {e.status}: {msg}) — SUPABASE_SERVICE_ROLE_KEY 가 맞는지 확인하세요."
    if e.status == 404 or p.get("code") in ("42P01", "PGRST205"):
        return f"Supabase 테이블 {table} 이 없습니다(HTTP {e.status}: {msg}) — supabase 마이그레이션을 적용했는지 확인하세요."
    if e.status == 400:
        return f"Supabase {table} 조회 형식 오류(HTTP 400: {msg}) — 열 이름이 테이블 계약과 같은지 확인하세요."
    return f"Supabase {table} 읽기 실패(HTTP {e.status or '네트워크'}: {msg}) — 잠시 뒤 다시 실행하세요."


def _range(col: str, start: datetime, end: datetime) -> list[tuple[str, str]]:
    return [(col, f"gte.{ts(start)}"), (col, f"lt.{ts(end)}")]


def plan_requests(ctx: CollectContext) -> list[str]:
    p, w = ctx.period, timedelta(days=ctx.settings.window_days)
    base = "<SUPABASE_URL>" + REST_PATH
    return [
        describe_request("GET", base.format(table="purchases"), [("select", "…"), *_range("created_at", p.start, p.end + PURCHASE_LAG)]),
        describe_request("GET", base.format(table="app_installs"), [("select", "…"), *_range("created_at", p.start, p.end)]),
        describe_request("GET", base.format(table="app_installs"), [("select", "…"), ("app_user_id", "in.(<기간 결제자 중 기간 밖 설치>)")]),
        describe_request("GET", base.format(table="link_clicks"), [("select", "…"), *_range("created_at", p.start - w, p.end)]),
        describe_request("GET", base.format(table="link_clicks"), [("select", "…"), ("content", "in.(<예전 설치의 utm_content>)")]),
    ]


def collect(ctx: CollectContext) -> dict[str, Any]:
    if ctx.dry_run:
        reqs = plan_requests(ctx)
        ctx.log.extend(f"[funnel] {r} (Range: 0-{PAGE_SIZE - 1})" for r in reqs)
        return {"data": {}, "requests": reqs, "warnings": ["dry-run: 요청을 보내지 않았습니다."]}
    if ctx.fake:
        data = ctx.fake.funnel()
        return {"data": data, "counts": {k: len(v) for k, v in data.items() if isinstance(v, list)}, "warnings": []}
    data = fetch_all(_Rest().get, ctx)
    if ctx.settings.hash_user_ids:
        for table in ("app_installs", "purchases"):
            for r in data[table]:
                r["app_user_id"] = hash_uid(r.get("app_user_id"))
    return {"data": data, "counts": {k: len(v) for k, v in data.items() if isinstance(v, list)}, "warnings": [],
            "hashed_user_ids": ctx.settings.hash_user_ids}


def fetch_all(get: Getter, ctx: CollectContext) -> dict[str, Any]:
    p, window = ctx.period, timedelta(days=ctx.settings.window_days)
    purchases = get("purchases", PURCHASE_COLUMNS, _range("created_at", p.start, p.end + PURCHASE_LAG))
    installs = get("app_installs", INSTALL_COLUMNS, _range("created_at", p.start, p.end))
    have = {r.get("app_user_id") for r in installs}
    need = sorted({str(r["app_user_id"]) for r in purchases if r.get("app_user_id") and r.get("app_user_id") not in have})
    prior: list[dict[str, Any]] = []
    for i in range(0, len(need), IN_CHUNK):
        prior += get("app_installs", INSTALL_COLUMNS, [("app_user_id", pg_in(need[i:i + IN_CHUNK]))])
    click_start = p.start - window
    clicks = get("link_clicks", CLICK_COLUMNS, _range("created_at", click_start, p.end))
    # 기간 전 설치는 귀속 창이 위 클릭 범위보다 앞이므로, 그 콘텐츠의 클릭만 그 시점 범위로 더 읽는다
    contents = sorted({str(r["utm_content"]).strip() for r in prior if r.get("utm_content")})
    times = [t for t in (parse_time(r.get("created_at")) for r in prior) if t]
    if contents and times:
        lo, hi = min(times) - window, min(max(times) + timedelta(minutes=10), click_start)
        seen = {r.get("id") for r in clicks}
        for i in range(0, len(contents), IN_CHUNK):
            for r in get("link_clicks", CLICK_COLUMNS, [("content", pg_in(contents[i:i + IN_CHUNK])), *_range("created_at", lo, hi)]):
                if r.get("id") not in seen:
                    clicks.append(r)
                    seen.add(r.get("id"))
    return {
        "link_clicks": clicks,
        "app_installs": installs + [r for r in prior if r.get("app_user_id") not in have],
        "purchases": purchases,
        "ranges": {
            "purchases_created": [ts(p.start), ts(p.end + PURCHASE_LAG)],
            "installs_created": [ts(p.start), ts(p.end)],
            "clicks_created": [ts(click_start), ts(p.end)],
            "prior_installs": len(prior),
        },
    }
