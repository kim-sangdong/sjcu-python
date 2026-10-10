"""분석 모듈 공통: 집계 기간, 설정값, 발행 대기열 매핑, HTTP 도우미.

설정(channel.yaml, 모두 선택 — 없으면 아래 기본값):
  analytics:
    usd_krw: 1400               # 원화 환산 환율 가정(보고서에 그대로 표시)
    click_window_days: 7        # 클릭 후 이 기간 안의 설치만 그 콘텐츠로 귀속
    require_click: true         # utm 이 있어도 창 안의 클릭 기록이 없으면 '창 밖'(미귀속)
    include_sandbox: false      # RevenueCat SANDBOX 결제를 매출에 넣을지
    youtube_revenue: false      # 유튜브 수익 지표(yt-analytics-monetary.readonly 범위 필요, YPP 가입 후)
    media_lookback_weeks: 8     # 인스타·페북·틱톡 게시물 지표를 몇 주 전 게시물까지 조회할지
    hash_user_ids: true         # metrics.json 에 app_user_id 대신 해시를 저장(보고서는 커밋 대상)
  ads:
    weekly_budget_krw: 0
    cpm_krw: 4000               # 가정값 — 실제 집행 후 보정
    cpi_krw: 2500
    rules: {min_clicks: 100, min_installs: 20, boost_roas: 1.5, stop_roas: 0.5, min_paid_for_boost: 3}
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from pydantic import ValidationError

from studio import paths
from studio.config import Config
from studio.models import PublishQueue, read_model

# ---------------------------------------------------------------- 상수

CONTENT_SOURCES = ("youtube", "instagram", "facebook", "tiktok")    # 게시물 지표 원천
ALL_SOURCES = (*CONTENT_SOURCES, "funnel")
FUNNEL_PLATFORMS = ("youtube", "instagram", "facebook", "tiktok", "tistory")
KIND_LABEL = {"longform": "롱폼", "short": "쇼츠", "cardnews": "카드뉴스", "blog": "블로그"}
PLATFORM_LABEL = {"youtube": "유튜브", "instagram": "인스타그램", "facebook": "페이스북", "tiktok": "틱톡",
                  "tistory": "티스토리", "other": "기타(원천 불명)"}
SOURCE_LABEL = {**PLATFORM_LABEL, "funnel": "클릭·설치·결제(Supabase)"}

DEFAULT_USD_KRW = 1400.0
DEFAULT_WINDOW_DAYS = 7
DEFAULT_MEDIA_LOOKBACK_WEEKS = 8
DEFAULT_CPM_KRW = 4000.0
DEFAULT_CPI_KRW = 2500.0
DEFAULT_RULES: dict[str, float] = {
    "min_clicks": 100,          # 표본 충분: 클릭 이 값 이상
    "min_installs": 20,         # 그리고 설치 이 값 이상
    "boost_roas": 1.5,          # 추정 ROAS 가 이 값 이상이고
    "min_paid_for_boost": 3,    # 유료 전환이 이 값 이상이면 boost
    "stop_roas": 0.5,           # 추정 ROAS 가 이 값 미만이면(또는 유료 전환 0) stop
}

ASSET_ID_RE = re.compile(r"^\d{4}W\d{2}-(?:L\d+|[SCB]\d{2})$")
PUBLISHED_STATES = ("published", "scheduled")   # scheduled 는 publish_at 이 지났으면 공개된 것으로 본다

# utm_source·link_clicks.source 값 → 플랫폼. 앱(app/src/core/referrer.ts)의 yt_shorts 같은 세부 값도 받는다.
SOURCE_PREFIXES = (
    ("youtube", "youtube"), ("yt", "youtube"),
    ("instagram", "instagram"), ("ig", "instagram"),
    ("facebook", "facebook"), ("fb", "facebook"),
    ("tiktok", "tiktok"), ("tt", "tiktok"),
    ("tistory", "tistory"), ("blog", "tistory"),
)
ORGANIC_SOURCES = ("google-play", "google_play", "play-store", "(direct)")

HTTP_TIMEOUT = (10, 60)
HTTP_RETRIES = 2

_sleep = time.sleep   # 테스트에서 바꿔 끼운다


# ---------------------------------------------------------------- 기간

@dataclass(frozen=True)
class Period:
    """집계 기간 [start, end). 주차의 월요일 00:00 KST 부터 다음 월요일 00:00 까지, 진행 중이면 수집 시각까지."""

    week: str
    start: datetime
    end: datetime
    full_end: datetime

    @property
    def partial(self) -> bool:
        return self.end < self.full_end

    @property
    def start_date(self) -> date:
        return self.start.astimezone(paths.KST).date()

    @property
    def end_date(self) -> date:
        """마지막 날(포함)."""
        return (self.end - timedelta(microseconds=1)).astimezone(paths.KST).date()

    def contains(self, t: datetime | None) -> bool:
        return t is not None and self.start <= t < self.end

    def to_json(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "full_end": self.full_end.isoformat(),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "partial": self.partial,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Period:
        return cls(d["week"], _req_time(d["start"]), _req_time(d["end"]), _req_time(d.get("full_end") or d["end"]))


def week_period(week: str, now: datetime | None = None) -> Period:
    start = datetime.combine(paths.week_monday(week), datetime.min.time(), tzinfo=paths.KST)
    full_end = start + timedelta(days=7)
    now = (now or paths.now_kst()).astimezone(paths.KST)
    if now <= start:
        raise ValueError(f"{week} 는 아직 시작하지 않은 주차입니다({start:%Y-%m-%d} 시작). 지난 주차나 이번 주차를 지정하세요.")
    return Period(week, start, min(full_end, now.replace(microsecond=0)), full_end)


def current_week(now: datetime | None = None) -> str:
    """분석 기본 주차 = 지금이 속한 ISO 주(발행 중인 주)."""
    return paths.week_of(now or paths.now_kst())


# ---------------------------------------------------------------- 설정

def _extra(cfg: Config, name: str) -> dict[str, Any]:
    v = (cfg.model_extra or {}).get(name)
    return dict(v) if isinstance(v, dict) else {}


def _num(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


@dataclass(frozen=True)
class Settings:
    usd_krw: float = DEFAULT_USD_KRW
    window_days: int = DEFAULT_WINDOW_DAYS
    require_click: bool = True
    include_sandbox: bool = False
    youtube_revenue: bool = False
    media_lookback_weeks: int = DEFAULT_MEDIA_LOOKBACK_WEEKS
    hash_user_ids: bool = True
    cpm_krw: float = DEFAULT_CPM_KRW
    cpi_krw: float = DEFAULT_CPI_KRW
    weekly_budget_krw: float = 0.0
    rules: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RULES))


def settings(cfg: Config) -> Settings:
    a, ads = _extra(cfg, "analytics"), _extra(cfg, "ads")
    rules = dict(DEFAULT_RULES)
    for k, v in (ads.get("rules") or {}).items():
        if k in rules:
            rules[k] = _num(v, rules[k])
    usd_krw = _num(a.get("usd_krw"), DEFAULT_USD_KRW)
    if usd_krw <= 0:
        raise ValueError("analytics.usd_krw 는 0보다 커야 합니다.")
    return Settings(
        usd_krw=usd_krw,
        window_days=max(1, int(_num(a.get("click_window_days"), DEFAULT_WINDOW_DAYS))),
        require_click=_bool(a.get("require_click"), True),
        include_sandbox=_bool(a.get("include_sandbox"), False),
        youtube_revenue=_bool(a.get("youtube_revenue"), False),
        media_lookback_weeks=max(1, int(_num(a.get("media_lookback_weeks"), DEFAULT_MEDIA_LOOKBACK_WEEKS))),
        hash_user_ids=_bool(a.get("hash_user_ids"), True),
        cpm_krw=_num(ads.get("cpm_krw"), DEFAULT_CPM_KRW),
        cpi_krw=_num(ads.get("cpi_krw"), DEFAULT_CPI_KRW),
        weekly_budget_krw=_num(ads.get("weekly_budget_krw"), 0.0),
        rules=rules,
    )


def platform_enabled(cfg: Config, platform: str) -> bool:
    p = getattr(cfg.platforms, platform, None)
    return bool(getattr(p, "enabled", True)) if p is not None else True


# ---------------------------------------------------------------- 값 도우미

def is_asset_id(v: Any) -> bool:
    return isinstance(v, str) and bool(ASSET_ID_RE.match(v.strip()))


def kind_of(asset_id: str | None) -> str | None:
    if not is_asset_id(asset_id):
        return None
    tag = str(asset_id).rsplit("-", 1)[-1][:1]
    return {"L": "longform", "S": "short", "C": "cardnews", "B": "blog"}[tag]


def content_week(asset_id: str | None) -> str | None:
    """'2026W42-S03' → '2026-W42'."""
    if not is_asset_id(asset_id):
        return None
    head = str(asset_id).split("-", 1)[0]
    return f"{head[:4]}-{head[4:]}"


def norm_source(v: Any) -> str:
    """utm_source → 플랫폼 이름. 빈 값은 '', 모르는 값은 'other'."""
    s = str(v or "").strip().lower()
    if not s:
        return ""
    for prefix, platform in SOURCE_PREFIXES:
        if s == prefix or s.startswith((prefix + "_", prefix + "-", prefix + ".")):
            return platform
    return "other"


def parse_time(v: Any) -> datetime | None:
    """ISO 문자열·epoch(초/밀리초) → aware datetime(UTC 기본). 못 읽으면 None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, (int, float)):
        sec = v / 1000 if v > 1e11 else v
        return datetime.fromtimestamp(sec, UTC)
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _req_time(v: Any) -> datetime:
    t = parse_time(v)
    if t is None:
        raise ValueError(f"시각 형식이 아닙니다: {v!r}")
    return t


def num(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or not b:
        return None
    return a / b


def env_dry_run() -> bool:
    return os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def iso_now() -> str:
    return datetime.now(paths.KST).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 발행 대기열 → (플랫폼, 원격 ID) 매핑

@dataclass(frozen=True)
class Target:
    asset_id: str
    kind: str
    platform: str
    remote_id: str
    publish_at: datetime
    status: str
    url: str | None = None
    week: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"asset_id": self.asset_id, "kind": self.kind, "platform": self.platform, "remote_id": self.remote_id,
                "publish_at": self.publish_at.isoformat(), "status": self.status, "url": self.url, "week": self.week}


def queue_files() -> list[Path]:
    return sorted((paths.CONTENT / "weeks").glob("*/queue.json"))


def load_targets(*, until: datetime, since: datetime | None = None,
                 platforms: Iterable[str] | None = None) -> tuple[list[Target], list[str]]:
    """모든 주차의 queue.json 에서 공개된(또는 예약 시각이 지난) 게시물 목록. (목록, 경고)."""
    want = set(platforms) if platforms else None
    out: list[Target] = []
    warnings: list[str] = []
    for f in queue_files():
        try:
            q = read_model(f, PublishQueue)
        except (ValidationError, ValueError, OSError) as e:
            warnings.append(f"{f.relative_to(paths.ROOT) if f.is_relative_to(paths.ROOT) else f}: 읽지 못함({type(e).__name__}) — 이 주차 게시물은 매핑에서 빠집니다.")
            continue
        for t in q.targets:
            if want and t.platform not in want:
                continue
            rid = (t.remote_id or "").strip()
            if not rid or rid.startswith("dry-run") or t.status not in PUBLISHED_STATES:
                continue
            at = t.publish_at if t.publish_at.tzinfo else t.publish_at.replace(tzinfo=paths.KST)
            if at > until or (since and at < since):
                continue
            out.append(Target(t.asset_id, t.asset_kind, t.platform, rid, at, t.status, t.url, q.week))
    return out, warnings


def remote_map(targets: Iterable[Target]) -> dict[tuple[str, str], Target]:
    return {(t.platform, t.remote_id): t for t in targets}


# ---------------------------------------------------------------- HTTP

class HttpError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, payload: Any = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.retryable = retryable


_SECRET_QS = re.compile(r"(access_token|client_secret|refresh_token|apikey|key)=[^&\s'\"]+", re.IGNORECASE)


def redact(text: str, secrets: Iterable[str | None] = ()) -> str:
    text = _SECRET_QS.sub(r"\1=***", text)
    for s in secrets:
        if s and len(s) >= 8:
            text = text.replace(s, "***")
    return text


def request_json(method: str, url: str, *, what: str, params: Any = None, headers: dict[str, str] | None = None,
                 json_body: Any = None, secrets: Iterable[str | None] = (), retries: int = HTTP_RETRIES,
                 timeout: Any = HTTP_TIMEOUT, ok_status: tuple[int, ...] = ()) -> tuple[Any, requests.Response]:
    """JSON 요청. 네트워크 오류·429·5xx 는 retries 번 더 시도한다. 실패하면 HttpError(비밀값 제거된 메시지)."""
    secrets = list(secrets)
    last: HttpError | None = None
    for i in range(retries + 1):
        if i:
            _sleep(min(2 ** i, 20))
        try:
            r = requests.request(method, url, params=params, headers=headers, json=json_body, timeout=timeout)
        except requests.RequestException as e:
            last = HttpError(f"{what}: 네트워크 오류 {type(e).__name__}: {redact(str(e), secrets)[:200]}", retryable=True)
            continue
        try:
            payload = r.json() if r.content else None
        except ValueError:
            payload = None
        if r.ok or r.status_code in ok_status:
            return payload, r
        retryable = r.status_code == 429 or r.status_code >= 500
        body = payload if payload is not None else (r.text or "")[:300]
        last = HttpError(redact(f"{what} 실패 (HTTP {r.status_code}): {str(body)[:400]}", secrets),
                         status=r.status_code, payload=payload, retryable=retryable)
        if not retryable:
            break
    assert last is not None
    raise last


def describe_request(method: str, url: str, params: Any = None, body: Any = None) -> str:
    """dry-run 기록용 요청 요약(토큰 제외)."""
    items = params.items() if isinstance(params, dict) else (params or [])
    qs = " ".join(f"{k}={v}" for k, v in items if k not in ("access_token", "apikey"))
    text = f"{method} {url}" + (f" {qs}" if qs else "")
    if body is not None:
        text += f" body={body}"
    return text
