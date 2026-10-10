"""수집 실행기 — 원천별 수집기를 차례로 돌려 reports/<week>/metrics.json 에 모은다.

수집기 모듈(youtube, instagram, facebook, tiktok, funnel)은 collect(ctx) -> dict 를 내보낸다.
- 돌려주는 dict: rows(콘텐츠 지표, 정규화) 또는 data(funnel 원자료), raw, warnings, requests(dry-run), partial
- 실패하면 예외를 던진다. 실행기가 잡아서 {"ok": false, "error": ...} 로 기록하고 다음 원천을 계속한다.
- ctx.dry_run 이면 네트워크 없이 보낼 요청만 requests 에 적는다. ctx.fake 가 있으면 합성 응답을 쓴다.

metrics.json 구조:
  {"schema": 1, "week", "period": {...}, "collected_at", "fake", "settings": {...},
   "mapping": {"targets": n, "warnings": [...]},
   "sources": {"youtube": {"ok", "collected_at", "fake", "dry_run", "error", "warnings", "rows", "raw", ...}, ...}}
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from studio import paths
from studio.analytics.common import (
    ALL_SOURCES,
    CONTENT_SOURCES,
    Period,
    Settings,
    Target,
    iso_now,
    load_targets,
    platform_enabled,
    remote_map,
    settings,
    week_period,
)
from studio.config import Config, MissingSecret, env, load_config
from studio.models import read_json, write_json

SCHEMA = 1
COLLECTORS = {s: f"studio.analytics.collect.{s}" for s in ALL_SOURCES}
# dry-run 때 미리 알려 줄 비밀값(값은 보지 않고 있는지만). 토큰 저장소(.secrets·Supabase)에 있으면 없어도 될 수 있다.
REQUIRED_ENV = {
    "youtube": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"),
    "instagram": ("INSTAGRAM_TOKEN",),
    "facebook": ("FACEBOOK_PAGE_TOKEN",),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"),
    "funnel": ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"),
}
METRIC_FIELDS = ("views", "reach", "likes", "comments", "shares", "saves", "watch_minutes",
                 "avg_view_seconds", "avg_view_pct", "subs_gained", "subs_lost")


class CollectRefused(RuntimeError):
    """덮어쓰면 안 되는 상황(실제 수집본 위에 합성 데이터 등)."""


@dataclass
class CollectContext:
    week: str
    period: Period
    cfg: Config
    settings: Settings
    targets: list[Target]
    dry_run: bool = False
    fake: Any = None                     # studio.analytics.collect.fake.FakeWorld
    log: list[str] = field(default_factory=list)

    @property
    def id_map(self) -> dict[tuple[str, str], Target]:
        return remote_map(self.targets)

    def media_targets(self, platform: str) -> list[Target]:
        """게시물 단위로 조회할 대상(최근 media_lookback_weeks 주 안에 공개된 것)."""
        since = self.period.end - timedelta(weeks=self.settings.media_lookback_weeks)
        return sorted((t for t in self.targets if t.platform == platform and t.publish_at >= since),
                      key=lambda t: (t.publish_at, t.asset_id))


def content_row(platform: str, remote_id: str, target: Target | None, *, lifetime: bool,
                values: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """정규화된 콘텐츠 지표 한 줄. lifetime=True 면 누적값(보고서가 지난주 수집값과의 차이로 주간 값을 낸다)."""
    row: dict[str, Any] = {
        "platform": platform,
        "remote_id": remote_id,
        "asset_id": target.asset_id if target else None,
        "kind": target.kind if target else None,
        "published_at": target.publish_at.isoformat() if target else None,
        "url": target.url if target else None,
        "lifetime": lifetime,
    }
    for f in METRIC_FIELDS:
        v = values.get(f)
        row[f] = int(v) if isinstance(v, float) and v.is_integer() else v   # 1500.0 → 1500 (JSON 가독성)
    if extra:
        row["extra"] = extra
    return row


def metrics_path(week: str) -> Path:
    return paths.reports_dir(week) / "metrics.json"


def dry_run_path(week: str) -> Path:
    return paths.reports_dir(week) / "metrics.dry-run.json"


def load_metrics(week: str) -> dict[str, Any] | None:
    data = read_json(metrics_path(week))
    return data if isinstance(data, dict) and data.get("sources") is not None else None


def _err_text(e: BaseException) -> str:
    text = str(e).strip() or type(e).__name__
    if isinstance(e, MissingSecret) and not text.startswith("[설정 필요]"):
        text = f"[설정 필요] {text}"
    return text[:800]


def _real_sources(m: dict[str, Any]) -> bool:
    return any(s.get("ok") and not s.get("fake") and not s.get("dry_run") for s in (m.get("sources") or {}).values())


def is_fresh(week: str, sources: list[str], max_age_hours: float, *, fake: bool) -> bool:
    """최근 수집본이 있고 요청 원천이 모두 성공했으면 True(다시 수집할 필요 없음)."""
    m = load_metrics(week)
    if not m:
        return False
    for s in sources:
        res = (m["sources"] or {}).get(s)
        if not res or not res.get("ok") or bool(res.get("fake")) != fake:
            return False
        at = res.get("collected_at")
        try:
            age = datetime.now(paths.KST) - datetime.fromisoformat(at)
        except (TypeError, ValueError):
            return False
        if age > timedelta(hours=max_age_hours):
            return False
    return True


def run(week: str, *, sources: list[str] | None = None, fake: bool = False, dry_run: bool = False,
        cfg: Config | None = None, now: datetime | None = None, force: bool = False) -> tuple[dict[str, Any], Path]:
    """수집하고 파일에 쓴다. (metrics, 쓴 파일 경로)."""
    cfg = cfg or load_config()
    st = settings(cfg)
    period = week_period(week, now)
    sources = list(sources or ALL_SOURCES)
    unknown = [s for s in sources if s not in COLLECTORS]
    if unknown:
        raise ValueError(f"모르는 원천: {', '.join(unknown)} (가능: {', '.join(ALL_SOURCES)})")
    dry_run = dry_run and not fake          # 합성 데이터는 원래 네트워크를 쓰지 않는다

    existing = load_metrics(week)
    if fake and existing and _real_sources(existing) and not force:
        raise CollectRefused(f"{metrics_path(week)} 에 실제 수집 자료가 있어 합성 데이터로 덮어쓰지 않습니다(--force 로 강제).")

    targets, map_warnings = load_targets(until=period.end)
    world = None
    if fake:
        from studio.analytics.collect.fake import FakeWorld, synth_targets

        if not any(t.publish_at >= period.start for t in targets):
            targets = targets + synth_targets(week, cfg, period)
        world = FakeWorld(week, period, targets, st)
    ctx = CollectContext(week, period, cfg, st, targets, dry_run=dry_run, fake=world)

    results: dict[str, dict[str, Any]] = {}
    for s in sources:
        t0 = time.time()
        if s in CONTENT_SOURCES and not platform_enabled(cfg, s):
            res: dict[str, Any] = {"ok": True, "skipped": True, "note": f"platforms.{s}.enabled=false — 수집하지 않음", "rows": []}
        else:
            try:
                mod = importlib.import_module(COLLECTORS[s])
                res = dict(mod.collect(ctx))
                res["ok"] = True
                res.setdefault("error", None)
            except Exception as e:  # noqa: BLE001 — 한 원천이 실패해도 나머지는 계속한다
                res = {"ok": False, "error": _err_text(e)}
        res.update(collected_at=iso_now(), period_end=period.end.isoformat(), fake=fake, dry_run=dry_run,
                   seconds=round(time.time() - t0, 2))
        res.setdefault("warnings", [])
        if dry_run and not res.get("skipped"):
            missing = [n for n in REQUIRED_ENV.get(s, ()) if not env(n, required=False)]
            if missing:
                res["warnings"].append(f"환경변수 {', '.join(missing)} 가 없습니다 — 실제 실행에서 실패할 수 있습니다(토큰 저장소에 있으면 괜찮음).")
        results[s] = res

    # 이번에 수집하지 않은 원천은 기존 파일에서 이어받는다. 실제 수집과 합성 데이터는 섞지 않는다.
    # 이번에 실패했지만 기존 수집본이 성공이었으면 기존 것을 유지하고 실패 사실(last_error)만 붙인다(보고서가 경고).
    merged: dict[str, Any] = {}
    if existing and not dry_run:
        merged = {k: v for k, v in (existing.get("sources") or {}).items() if bool(v.get("fake")) == fake}
    for s, res in results.items():
        old = merged.get(s)
        if not res["ok"] and old and old.get("ok") and not old.get("skipped"):
            merged[s] = {**old, "last_error": res["error"], "last_attempt_at": res["collected_at"]}
        else:
            merged[s] = res

    metrics = {
        "schema": SCHEMA,
        "week": week,
        "period": period.to_json(),
        "collected_at": iso_now(),
        "fake": fake,
        "dry_run": dry_run,
        "settings": {"usd_krw": st.usd_krw, "click_window_days": st.window_days, "require_click": st.require_click,
                     "include_sandbox": st.include_sandbox, "youtube_revenue": st.youtube_revenue,
                     "media_lookback_weeks": st.media_lookback_weeks, "hash_user_ids": st.hash_user_ids},
        "mapping": {"targets": len(targets), "warnings": map_warnings},
        "sources": merged,
    }
    if dry_run:
        metrics["requests"] = ctx.log
    path = dry_run_path(week) if dry_run else metrics_path(week)
    write_json(path, metrics)
    return metrics, path
