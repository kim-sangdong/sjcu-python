"""발행 대기열(content/weeks/<week>/queue.json) — 만들기·잠금·처리.

계획(plan.json) 항목 × 켜진 플랫폼(cfg.platforms.<p>.enabled) × 플랫폼이 다루는 종류(PLATFORM_KINDS) = 발행 대상.

상태 흐름
  pending ──publish()──▶ uploading(작업 중 표시) ──▶ scheduled | published | processing | skipped
                                                  └─ 오류 ─▶ pending(재시도 대기) | failed
  processing ──check()──▶ scheduled | published | processing | failed
  uploading 이 UPLOADING_STALE 넘게 그대로면 작업이 끊긴 것 → failed(중복 업로드를 막으려고 자동 재시도하지 않는다)

attempts 는 '연속 실패 횟수'다(성공하면 0). 재시도 가능한 오류(PublishError.retryable=True)는
attempts 가 MAX_ATTEMPTS 가 될 때까지 updated_at 기준 5·15·45·135분 뒤에 다시 시도하고, 그다음은 failed 로 고정한다.
retryable=False 는 바로 failed — 원인을 고친 뒤 `publish retry` 로 되살린다.

skipped 의 사유는 last_error 에 남긴다. AUTO_SKIP 으로 시작하는 사유(계획에서 빠짐·플랫폼 꺼짐·독창성 차단)는
조건이 풀리면 build_queue 가 pending 으로 되돌린다. 발행기가 돌려준 skipped(예: 유튜브 업로드 패키지)는 NOTE_PREFIX 로 남긴다.

dry_run 은 queue.json 을 바꾸지 않는다. 결과는 out/weeks/<week>/queue.dryrun.json 에 따로 쓴다
(가짜 ID 가 진짜 대기열에 섞이면 이후 실제 발행이 건너뛰어지기 때문).
"""

from __future__ import annotations

import fcntl
from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator

from pydantic import ValidationError

from studio import paths, planning
from studio.config import Config, MissingSecret
from studio.manifest import load_manifest
from studio.models import (
    OriginalityReport,
    PlanItem,
    PublishQueue,
    PublishTarget,
    RenderedAsset,
    WeekPlan,
    read_model,
    write_model,
)
from studio.publish import registry
from studio.publish.base import PublishContext, PublishError, PublishResult, Publisher

# ---------------------------------------------------------------- 상수

PLATFORM_KINDS: dict[str, tuple[str, ...]] = {
    "youtube": ("longform", "short"),
    "instagram": ("short", "cardnews"),
    "facebook": ("short",),
    "tiktok": ("short",),
    "tistory": ("blog",),
}
PRESCHEDULE_PLATFORMS = ("youtube", "facebook")     # 플랫폼이 예약 공개를 받는다(publishAt, scheduled_publish_time)
MEDIA_PLATFORMS = ("instagram", "facebook")          # 공개 URL(upload-media)로 파일을 가져간다
DEFAULT_MAX_PER_RUN = {"youtube": 20, "instagram": 5, "facebook": 5, "tiktok": 5, "tistory": 2}  # platforms.<p>.max_per_run 로 덮어쓴다
MAX_ATTEMPTS = 5
BACKOFF_MINUTES = (5, 15, 45, 135)
DEFAULT_LEAD = timedelta(minutes=10)
UPLOADING_STALE = timedelta(hours=3)
RECENT_WEEKS = 3
ERROR_MAX_CHARS = 600

ACTIVE_STATUSES = ("pending", "uploading", "processing")
LOCKED_STATUSES = ("published", "scheduled", "processing", "uploading")   # 병합 때 건드리지 않는다
RESULT_STATUSES = ("scheduled", "published", "processing", "skipped")    # 발행기가 돌려줄 수 있는 상태

AUTO_SKIP = "[자동 제외] "
SKIP_REMOVED = AUTO_SKIP + "계획(plan.json)에서 빠졌습니다."
SKIP_DISABLED = AUTO_SKIP + "channel.yaml platforms.{platform}.enabled 가 false 입니다."
SKIP_BLOCKED = AUTO_SKIP + "독창성 검사 차단 — originality.md 의 수정 지시대로 고치고 다시 검사하세요."
NOTE_PREFIX = "[안내] "

QUEUE_FILE = "queue.json"
DRYRUN_FILE = "queue.dryrun.json"
LOCK_FILE = "queue.lock"


def _now() -> datetime:
    return paths.now_kst()


def aware(dt: datetime) -> datetime:
    """시간대 없는 값은 KST 로 본다."""
    return dt.replace(tzinfo=paths.KST) if dt.tzinfo is None else dt


def backoff(attempts: int) -> timedelta:
    """attempts 번째 연속 실패 뒤 기다릴 시간: 5·15·45·135분."""
    i = min(max(attempts, 1), len(BACKOFF_MINUTES)) - 1
    return timedelta(minutes=BACKOFF_MINUTES[i])


def retry_at(t: PublishTarget) -> datetime | None:
    if t.attempts <= 0 or t.status not in ("pending", "processing") or t.updated_at is None:
        return None
    return aware(t.updated_at) + backoff(t.attempts)


def platform_enabled(cfg: Config, platform: str) -> bool:
    return bool(getattr(getattr(cfg.platforms, platform, None), "enabled", False))


def max_per_run(cfg: Config, platform: str) -> int:
    sec = getattr(cfg.platforms, platform, None)
    return int(getattr(sec, "max_per_run", DEFAULT_MAX_PER_RUN.get(platform, 5)))


def sort_key(t: PublishTarget) -> tuple:
    order = list(PLATFORM_KINDS)
    return (aware(t.publish_at), order.index(t.platform) if t.platform in order else 99, t.asset_id)


def is_auto_skip(t: PublishTarget) -> bool:
    return t.status == "skipped" and (t.last_error or "").startswith(AUTO_SKIP)


# ---------------------------------------------------------------- 파일·잠금

def queue_path(week: str) -> Path:
    return paths.content_dir(week) / QUEUE_FILE


def dryrun_path(week: str) -> Path:
    return paths.out_dir(week) / DRYRUN_FILE


def lock_path(week: str) -> Path:
    # out/ 은 커밋하지 않으므로 잠금 파일이 저장소에 섞이지 않는다. 잠금은 같은 컴퓨터의 프로세스끼리만 의미가 있다.
    return paths.out_dir(week) / LOCK_FILE


@contextmanager
def _locked(week: str, mode: int) -> Iterator[None]:
    lp = lock_path(week)
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "a") as fh:
        fcntl.flock(fh, mode)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read(path: Path, week: str) -> PublishQueue:
    return read_model(path, PublishQueue) if path.exists() else PublishQueue(week=week)


def load_queue(week: str) -> PublishQueue:
    """공유 잠금으로 읽는다. editing() 블록 안에서는 부르지 않는다(같은 프로세스라도 교착)."""
    with _locked(week, fcntl.LOCK_SH):
        return _read(queue_path(week), week)


def save_queue(queue: PublishQueue) -> Path:
    with _locked(queue.week, fcntl.LOCK_EX):
        return write_model(queue_path(queue.week), queue)


@contextmanager
def editing(week: str) -> Iterator[PublishQueue]:
    """with editing(week) as q: ... — 배타 잠금으로 읽고, 블록이 정상 종료하면(바뀐 경우에만) 저장한다."""
    path = queue_path(week)
    with _locked(week, fcntl.LOCK_EX):
        q = _read(path, week)
        before = q.model_dump_json()
        yield q
        if q.model_dump_json() != before or (not path.exists() and q.targets):
            write_model(path, q)


def load_dryrun_base(week: str) -> PublishQueue:
    """리허설 시작점: 실제 대기열보다 새 리허설 결과가 있으면 그것을, 아니면 실제 대기열."""
    real, dry = queue_path(week), dryrun_path(week)
    if dry.exists() and (not real.exists() or dry.stat().st_mtime >= real.stat().st_mtime):
        return _read(dry, week)
    return load_queue(week)


def save_dryrun(queue: PublishQueue) -> Path:
    return write_model(dryrun_path(queue.week), queue)


# ---------------------------------------------------------------- 대기열 만들기·병합

def blocked_assets(week: str, notes: list[str] | None = None) -> set[str]:
    """originality.json 에서 차단(block)된 콘텐츠 ID."""
    p = paths.content_dir(week) / "originality.json"
    if not p.exists():
        if notes is not None:
            notes.append(f"독창성 검사 결과(originality.json)가 없습니다 — `python -m studio check originality --week {week}` 를 먼저 돌리세요.")
        return set()
    try:
        return {a.asset_id for a in read_model(p, OriginalityReport).blocked()}
    except (ValidationError, ValueError) as e:
        if notes is not None:
            notes.append(f"originality.json 을 읽지 못했습니다({type(e).__name__}) — 차단 목록 없이 진행합니다. 검사를 다시 돌리세요.")
        return set()


def _skip(t: PublishTarget, reason: str, now: datetime) -> None:
    t.status = "skipped"
    t.last_error = reason
    t.attempts = 0
    t.updated_at = now


def build_queue(
    week: str,
    cfg: Config,
    *,
    existing: PublishQueue | None = None,
    plan: WeekPlan | None = None,
    blocked: set[str] | None = None,
    notes: list[str] | None = None,
    now: datetime | None = None,
) -> PublishQueue:
    """계획 × 켜진 플랫폼 × 지원 종류로 대상을 만들고 기존 대기열과 상태 보존 병합한다(저장하지 않는다).

    - published·scheduled·processing·uploading 은 그대로 둔다.
    - 계획에서 빠졌거나 플랫폼이 꺼진 pending·failed 는 skipped(자동 제외), 다시 들어오면 pending 으로 되돌린다.
    - 독창성 검사 차단 콘텐츠는 skipped(자동 제외).
    - pending·failed 의 publish_at 은 계획의 새 시각으로 맞춘다.
    """
    plan = plan if plan is not None else planning.load_plan(week)
    existing = existing if existing is not None else load_queue(week)
    blocked = blocked if blocked is not None else blocked_assets(week, notes)
    notes = notes if notes is not None else []
    now = now or _now()

    enabled = [p for p in PLATFORM_KINDS if platform_enabled(cfg, p)]
    desired: dict[tuple[str, str], PlanItem] = {}
    for item in plan.items:
        if item.publish_at is None:
            notes.append(f"{item.id}: plan.json 에 publish_at 이 없어 대기열에 넣지 않았습니다.")
            continue
        for p in enabled:
            if item.kind in PLATFORM_KINDS[p]:
                desired[(p, item.id)] = item

    out = PublishQueue(week=week)
    seen: set[tuple[str, str]] = set()
    for old in existing.targets:
        t = old.model_copy(deep=True)
        k = (t.platform, t.asset_id)
        seen.add(k)
        out.targets.append(t)
        if t.status in LOCKED_STATUSES:
            continue
        item = desired.get(k)
        if item is None:
            if t.status in ("pending", "failed"):
                reason = SKIP_REMOVED if platform_enabled(cfg, t.platform) else SKIP_DISABLED.format(platform=t.platform)
                _skip(t, reason, now)
            continue
        t.asset_kind = item.kind
        if t.status in ("pending", "failed") or is_auto_skip(t):
            t.publish_at = aware(item.publish_at)  # type: ignore[arg-type]
        if item.id in blocked:
            if t.status in ("pending", "failed") or is_auto_skip(t):
                _skip(t, SKIP_BLOCKED, now)
        elif is_auto_skip(t):
            t.status, t.last_error, t.attempts, t.updated_at = "pending", None, 0, now
    for k, item in desired.items():
        if k in seen:
            continue
        t = PublishTarget(asset_id=item.id, asset_kind=item.kind, platform=k[0], publish_at=aware(item.publish_at))  # type: ignore[arg-type]
        if item.id in blocked:
            _skip(t, SKIP_BLOCKED, now)
        out.targets.append(t)
    out.targets.sort(key=sort_key)
    return out


def sync_queue(
    week: str, cfg: Config, *, dry_run: bool = False, notes: list[str] | None = None, now: datetime | None = None
) -> PublishQueue:
    """계획을 대기열에 반영한다. 실제 실행은 잠금 상태에서 queue.json 에, dry_run 은 메모리에만."""
    plan = planning.load_plan(week)
    blocked = blocked_assets(week, notes)
    if dry_run:
        return build_queue(week, cfg, existing=load_dryrun_base(week), plan=plan, blocked=blocked, notes=notes, now=now)
    with editing(week) as q:
        merged = build_queue(week, cfg, existing=q, plan=plan, blocked=blocked, notes=notes, now=now)
        q.targets = merged.targets
        return q.model_copy(deep=True)


# ---------------------------------------------------------------- 선택

def action_for(t: PublishTarget, now: datetime, *, lead: timedelta = DEFAULT_LEAD, preschedule: bool = False) -> str | None:
    """이번 실행에서 할 일: publish | check | stale | None."""
    if t.status == "uploading":
        if t.updated_at is None or now - aware(t.updated_at) >= UPLOADING_STALE:
            return "stale"
        return None
    if t.status not in ("pending", "processing"):
        return None
    ra = retry_at(t)
    if ra is not None and now < ra:
        return None
    if t.status == "processing":
        return "check"
    if preschedule or aware(t.publish_at) <= now + lead:
        return "publish"
    return None


def select(
    queue: PublishQueue,
    now: datetime,
    *,
    lead: timedelta = DEFAULT_LEAD,
    platforms: Iterable[str] | None = None,
    preschedule: bool = False,
) -> list[tuple[PublishTarget, str]]:
    want = set(platforms) if platforms else None
    picks = []
    for t in sorted(queue.targets, key=sort_key):
        if want is not None and t.platform not in want:
            continue
        a = action_for(t, now, lead=lead, preschedule=preschedule)
        if a:
            picks.append((t, a))
    return picks


def recent_weeks(now: datetime, n: int = RECENT_WEEKS) -> list[str]:
    """최근 n 주(오래된 것부터). 일요일에는 다음 주 묶음(그날 19시 롱폼)이 포함되도록 publishing_week 기준."""
    weeks = [paths.publishing_week(now)]
    for _ in range(n - 1):
        weeks.insert(0, paths.previous_week(weeks[0]))
    return weeks


# ---------------------------------------------------------------- 처리 결과

@dataclass
class Outcome:
    week: str
    platform: str
    asset_id: str
    action: str                       # publish | check | stale | limit | blocked
    status: str                       # 처리 뒤 상태
    ok: bool
    message: str = ""
    url: str | None = None
    retry_at: datetime | None = None
    log: list[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        if self.action == "limit":
            return "한도 대기"
        if self.status == "failed":
            return "실패"
        if not self.ok:
            return "재시도 대기"
        return {"published": "성공", "scheduled": "성공", "processing": "처리 중", "skipped": "건너뜀"}.get(self.status, "기타")


def summarize(outcomes: Iterable[Outcome]) -> Counter:
    return Counter(o.category for o in outcomes)


def _fail(t: PublishTarget, out: Outcome, message: str, retryable: bool, now: datetime, action: str) -> Outcome:
    t.attempts += 1
    t.updated_at = now
    if not retryable or t.attempts >= MAX_ATTEMPTS:
        if retryable:
            message += f" ({t.attempts}회 연속 실패 — 원인을 고친 뒤 `python -m studio publish retry --week {out.week} --asset {t.asset_id} --platform {t.platform}`)"
        t.status = "failed"
    else:
        t.status = "processing" if action == "check" else "pending"
        out.retry_at = now + backoff(t.attempts)
    t.last_error = message[:ERROR_MAX_CHARS]
    out.status, out.ok, out.message = t.status, False, message
    return out


def _apply(t: PublishTarget, res: PublishResult, now: datetime) -> None:
    t.status = res.status
    if res.remote_id:
        t.remote_id = res.remote_id
    if res.url:
        t.url = res.url
    t.attempts = 0
    t.updated_at = now
    t.last_error = (NOTE_PREFIX + res.note)[:ERROR_MAX_CHARS] if res.status == "skipped" and res.note else None


def build_context(week: str, target: PublishTarget, cfg: Config, *, dry_run: bool, log: list[str]) -> PublishContext:
    """manifest 의 렌더 결과와 원고를 읽어 PublishContext 를 만든다. 로컬 파일이 없으면 스토리지에서 받아 온다."""
    asset = load_manifest(week).get(target.asset_id)
    if asset is None:
        raise PublishError(
            f"{target.asset_id}: 렌더 결과가 manifest.json 에 없습니다. `python -m studio render ... --week {week}` 로 렌더한 뒤 "
            f"`python -m studio publish retry --week {week} --asset {target.asset_id} --platform {target.platform}` 하세요.",
            retryable=False,
        )
    src = planning.asset_path(week, target.asset_kind, target.asset_id)
    if not src.exists():
        raise PublishError(f"{target.asset_id}: 원고 파일이 없습니다({src}). 원고를 되살린 뒤 `publish retry` 하세요.", retryable=False)
    try:
        content = read_model(src, planning.KIND_MODEL[target.asset_kind])
    except (ValidationError, ValueError) as e:
        raise PublishError(
            f"{target.asset_id}: 원고 형식 오류({type(e).__name__}) — `python -m studio plan validate --week {week}` 로 확인하세요.",
            retryable=False,
        ) from None
    if not dry_run and asset.meta.get("tts_provider") == "silent":
        # 무음 TTS 는 리허설용 렌더 — 실제 발행되면 소리 없는 영상이 올라간다.
        raise PublishError(
            f"{target.asset_id}: 무음(silent) TTS 로 렌더된 리허설 결과입니다. 실제 음성으로 다시 렌더한 뒤 "
            f"`python -m studio publish retry --week {week} --asset {target.asset_id} --platform {target.platform}` 하세요.",
            retryable=False,
        )
    _restore_local(week, asset, dry_run=dry_run, log=log)
    return PublishContext(week=week, target=target, asset=asset, content=content, cfg=cfg, dry_run=dry_run, log=log)


def _restore_local(week: str, asset: RenderedAsset, *, dry_run: bool, log: list[str]) -> None:
    missing = [role for role, rel in asset.files.items() if not (paths.ROOT / rel).exists()]
    if not missing:
        return
    if dry_run:
        log.append(f"[dry-run] 로컬에 없는 파일({', '.join(missing)}) — 스토리지 내려받기 생략")
        return
    try:
        from studio.publish.storage import ensure_local
    except ImportError:
        log.append("studio.publish.storage 가 없어 로컬 파일을 내려받지 못했습니다.")
        return
    try:
        ensure_local(week, asset)
    except PublishError as e:
        # 공개 URL 로 올리는 플랫폼(인스타)은 로컬 파일이 없어도 된다 — 필요한 발행기가 스스로 오류를 낸다.
        log.append(f"로컬 파일 복원 실패({e}) — 발행기에 맡깁니다.")


def process(
    week: str,
    target: PublishTarget,
    cfg: Config,
    *,
    action: str = "publish",
    dry_run: bool = False,
    now: datetime | None = None,
    publisher: Publisher | None = None,
    blocked: Iterable[str] = (),
) -> Outcome:
    """대상 하나를 발행(publish)하거나 상태를 확인(check)하고 target 을 제자리에서 갱신한다. 예외를 밖으로 내지 않는다."""
    now = now or _now()
    out = Outcome(week, target.platform, target.asset_id, action, status=target.status, ok=False)
    if action == "publish" and target.asset_id in set(blocked):
        _skip(target, SKIP_BLOCKED, now)
        out.status, out.ok, out.message, out.action = "skipped", True, SKIP_BLOCKED, "blocked"
        return out
    try:
        ctx = build_context(week, target, cfg, dry_run=dry_run, log=out.log)
        pub = publisher or registry.get_publisher(target.platform)
        kinds = tuple(getattr(pub, "kinds", ()) or ())
        if kinds and target.asset_kind not in kinds:
            raise PublishError(f"{target.platform} 발행기는 {target.asset_kind} 를 다루지 않습니다(지원: {', '.join(kinds)}).", retryable=False)
        res = pub.publish(ctx) if action == "publish" else pub.check(ctx)
        if not isinstance(res, PublishResult) or res.status not in RESULT_STATUSES:
            raise PublishError(f"{target.platform} 발행기가 알 수 없는 결과를 돌려줬습니다({getattr(res, 'status', res)!r}).", retryable=False)
    except PublishError as e:
        return _fail(target, out, str(e), e.retryable, now, action)
    except MissingSecret as e:
        return _fail(target, out, f"[설정 필요] {e} 넣은 뒤 `publish retry` 하세요.", False, now, action)
    except ImportError as e:
        return _fail(target, out, f"{target.platform} 발행기를 불러오지 못했습니다({e}). 모듈·의존성을 설치한 뒤 `publish retry` 하세요.",
                     False, now, action)
    except Exception as e:  # noqa: BLE001 — 한 대상의 예상 못 한 오류가 전체 실행을 멈추지 않게
        return _fail(target, out, f"예상하지 못한 오류 {type(e).__name__}: {str(e)[:300]}", True, now, action)
    _apply(target, res, now)
    out.status, out.ok, out.url, out.message = target.status, True, target.url, res.note
    return out


def _fail_stale(t: PublishTarget, week: str, now: datetime) -> Outcome:
    since = aware(t.updated_at).strftime("%m-%d %H:%M") if t.updated_at else "알 수 없음"
    msg = (f"업로드 도중 작업이 끊겼습니다(시작 {since}). {t.platform} 에 올라갔는지 직접 확인하고, 올라갔으면 "
           f"`python -m studio publish mark --week {week} --asset {t.asset_id} --platform {t.platform} --remote-id <ID>`, "
           f"아니면 `python -m studio publish retry --week {week} --asset {t.asset_id} --platform {t.platform}` 하세요.")
    t.status, t.last_error, t.updated_at = "failed", msg[:ERROR_MAX_CHARS], now
    return Outcome(week, t.platform, t.asset_id, "stale", "failed", False, msg)


def _copy_state(src: PublishTarget, dst: PublishTarget) -> None:
    for name in ("status", "remote_id", "url", "attempts", "last_error", "updated_at", "publish_at", "asset_kind"):
        setattr(dst, name, getattr(src, name))


# ---------------------------------------------------------------- 미디어 업로드

def _media_roles_missing(asset: RenderedAsset) -> list[str]:
    if asset.kind == "short":
        roles = [r for r in ("video",) if r in asset.files]
    elif asset.kind == "cardnews":
        roles = sorted(r for r in asset.files if r.startswith("slide"))
    else:
        return []
    return [r for r in roles if not asset.remote_urls.get(r)]


def media_needed(week: str, targets: Iterable[PublishTarget], *, need_local: bool = False) -> list[str]:
    """공개 URL 이 필요한데 아직 없는 콘텐츠 ID. need_local=True 면 로컬 파일이 있어 올릴 수 있는 것만."""
    ids = sorted({t.asset_id for t in targets if t.platform in MEDIA_PLATFORMS and t.status in ("pending", "processing")})
    if not ids:
        return []
    manifest = load_manifest(week)
    out = []
    for i in ids:
        a = manifest.get(i)
        if a is None:
            continue
        missing = _media_roles_missing(a)
        if missing and (not need_local or any((paths.ROOT / a.files[r]).exists() for r in missing)):
            out.append(i)
    return out


def prepare_media(week: str, asset_ids: list[str], *, dry_run: bool, log: Callable[[str], None] = print) -> list[str]:
    """storage.upload_media 로 공개 URL 을 만든다. 문제 목록을 돌려준다(실패해도 발행 처리는 계속)."""
    if not asset_ids:
        return []
    log(f"미디어 업로드(공개 URL 필요): {', '.join(asset_ids)}")
    try:
        from studio.publish.storage import upload_media
        report = upload_media(week, asset_ids=asset_ids, dry_run=dry_run)
    except ImportError:
        return ["studio.publish.storage 가 없어 미디어를 올리지 못했습니다 — 인스타·페북 발행이 실패할 수 있습니다."]
    except MissingSecret as e:
        return [f"[설정 필요] 미디어 업로드: {e}"]
    except PublishError as e:
        return [f"미디어 업로드 실패: {e}"]
    problems = []
    for it in getattr(report, "failed", None) or []:
        problems.append(f"미디어 업로드 실패 {getattr(it, 'asset_id', '?')} {getattr(it, 'role', '?')}: {getattr(it, 'note', '')}")
    for i in getattr(report, "missing_ids", None) or []:
        problems.append(f"미디어 업로드: {i} 렌더 결과가 manifest 에 없습니다.")
    return problems


# ---------------------------------------------------------------- 실행

def run_week(
    week: str,
    cfg: Config,
    *,
    now: datetime | None = None,
    lead: timedelta = DEFAULT_LEAD,
    platforms: Iterable[str] | None = None,
    preschedule: bool = False,
    dry_run: bool = False,
    used: Counter | None = None,
    queue: PublishQueue | None = None,
    blocked: set[str] | None = None,
    media: bool = True,
    log: Callable[[str], None] = print,
) -> list[Outcome]:
    """대상 선택 → (잠금 안에서) uploading 표시 → 잠금 밖에서 발행 → (잠금 안에서) 결과 기록.

    used 는 실행 전체에서 플랫폼별 publish 건수(실행당 상한). dry_run 이면 queue(메모리, 없으면 리허설 시작점)만 바꾼다.
    """
    now = now or _now()
    used = used if used is not None else Counter()
    blocked = blocked if blocked is not None else blocked_assets(week)
    if dry_run:
        mem = queue if queue is not None else load_dryrun_base(week)

        def edit():
            return nullcontext(mem)
    else:
        def edit():
            return editing(week)

    with edit() as q:
        chosen = select(q, now, lead=lead, platforms=platforms, preschedule=preschedule)
        picks = [(t.platform, t.asset_id, a) for t, a in chosen]
        pending_media = [t.model_copy() for t, a in chosen if a == "publish"] if media else []
    if pending_media:
        ids = media_needed(week, pending_media, need_local=True)
        for p in prepare_media(week, ids, dry_run=dry_run, log=log):
            log(f"[경고] {p}")

    outcomes: list[Outcome] = []
    for platform, asset_id, action in picks:
        if action == "publish" and used[platform] >= max_per_run(cfg, platform):
            outcomes.append(Outcome(week, platform, asset_id, "limit", "pending", True,
                                    f"이번 실행의 {platform} 상한({max_per_run(cfg, platform)}건)에 도달 — 다음 실행에서 처리"))
            continue
        with edit() as q:
            t = q.find(platform, asset_id)
            if t is None or action_for(t, now, lead=lead, preschedule=preschedule) != action:
                continue   # 그 사이 다른 실행이 가져갔다
            if action == "stale":
                outcomes.append(_fail_stale(t, week, now))
                continue
            work = t.model_copy(deep=True)
            if not dry_run:
                t.status, t.updated_at = "uploading", now
        if action == "publish":
            used[platform] += 1
        out = process(week, work, cfg, action=action, dry_run=dry_run, now=now, blocked=blocked)
        with edit() as q:
            t = q.find(platform, asset_id)
            if t is None:
                q.targets.append(work)
            else:
                _copy_state(work, t)
        outcomes.append(out)
    if dry_run:
        save_dryrun(mem)
    return outcomes
