"""`python -m studio publish <하위명령>` — 발행 대기열.

  schedule --week W [--platform P] [--dry-run]
      계획을 대기열에 반영하고, 예약 공개를 받는 플랫폼(youtube publishAt, facebook scheduled_publish_time)은 바로 올린다.
      인스타·페북이 쓸 공개 URL 이 없으면 storage.upload_media 를 먼저 부른다. 나머지(instagram, tiktok, tistory)는 pending.
  due [--week W | --all-recent] [--now ISO] [--lead-minutes 10] [--platform P] [--dry-run]
      publish_at <= now + lead 인 pending(재시도 대기 포함)을 올리고, processing 은 check 로 확인한다.
      --week 가 없으면 최근 3주(--all-recent). GitHub Actions 가 30분마다 부르는 것을 전제로 한다.
  status --week W [--dry-run]
  retry  --week W (--asset ID --platform P | --all-failed) [--reupload] [--force]
  mark   --week W --asset ID --platform P --remote-id X [--url U] [--status published|scheduled]
  upload-media ...   (studio.publish.storage 가 붙인다)

모든 명령은 요약(성공·실패·대기 건수)을 출력하고, 실패가 있으면 종료 코드 1.
--dry-run(또는 STUDIO_DRY_RUN=1)은 queue.json 을 바꾸지 않고 결과를 out/weeks/<W>/queue.dryrun.json 에 쓴다.
"""

from __future__ import annotations

import argparse
import os
import re
from collections import Counter
from datetime import datetime, timedelta

from pydantic import ValidationError

from studio import paths, planning
from studio.cliutil import group
from studio.config import load_config
from studio.models import PublishQueue, PublishTarget, read_model
from studio.publish import queue as Q

STATUS_ORDER = ("pending", "uploading", "processing", "scheduled", "published", "skipped", "failed")
YOUTUBE_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|/video/)([A-Za-z0-9_-]{11})")
MARK_URLS = {
    ("youtube", "short"): "https://youtube.com/shorts/{id}",
    ("youtube", "longform"): "https://youtu.be/{id}",
}
WEEKDAYS = "월화수목금토일"


# ---------------------------------------------------------------- 공통

def _dry(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False)) or os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def parse_now(value: str | None) -> datetime:
    if not value:
        return Q._now()
    try:
        return Q.aware(datetime.fromisoformat(value.strip()))
    except ValueError:
        raise SystemExit(f"--now 형식이 아닙니다: {value!r} (예: 2026-10-12T11:55+09:00)") from None


def fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    k = Q.aware(dt).astimezone(paths.KST)
    return f"{k:%m-%d}({WEEKDAYS[k.weekday()]}) {k:%H:%M}"


def _week(args: argparse.Namespace) -> str:
    week = args.week or paths.publishing_week()
    if not paths.WEEK_RE.match(week):
        raise SystemExit(f"주차 형식이 아닙니다: {week!r} (예: 2026-W42)")
    return week


def _one_line(text: str) -> str:
    return " ⏎ ".join(part.strip() for part in str(text).splitlines() if part.strip())


def print_outcomes(outcomes: list[Q.Outcome]) -> None:
    for o in outcomes:
        tail = o.url or ""
        if o.category in ("실패", "재시도 대기", "한도 대기", "건너뜀") or not tail:
            tail = o.message or tail
        if o.retry_at:
            tail += f" (다음 시도 {fmt_time(o.retry_at)} 이후)"
        label = o.action if o.action in ("check", "stale") else o.status
        print(f"[{o.category}] {o.week} {o.asset_id:<14} {o.platform:<9} {label:<10} {_one_line(tail)}".rstrip())
        for line in o.log:
            print(f"    · {_one_line(line)}")


def print_summary(outcomes: list[Q.Outcome], label: str = "요약") -> int:
    c = Q.summarize(outcomes)
    print(f"{label}: 성공 {c['성공']} · 처리 중 {c['처리 중']} · 건너뜀 {c['건너뜀']} · "
          f"대기 {c['재시도 대기'] + c['한도 대기']}(재시도 {c['재시도 대기']}, 한도 {c['한도 대기']}) · 실패 {c['실패']}")
    return 1 if c["실패"] else 0


def print_queue_counts(q: PublishQueue, label: str = "대기열") -> None:
    by_platform: dict[str, Counter] = {}
    for t in q.targets:
        by_platform.setdefault(t.platform, Counter())[t.status] += 1
    if not by_platform:
        print(f"{label}: 비어 있음")
        return
    parts = []
    for p in Q.PLATFORM_KINDS:
        if p in by_platform:
            c = by_platform[p]
            parts.append(f"{p} " + "/".join(f"{s} {c[s]}" for s in STATUS_ORDER if c[s]))
    print(f"{label}: " + " | ".join(parts))


# ---------------------------------------------------------------- schedule

def cmd_schedule(args: argparse.Namespace) -> int:
    cfg = load_config()
    week = _week(args)
    dry = _dry(args)
    now = parse_now(args.now)
    notes: list[str] = []
    try:
        q = Q.sync_queue(week, cfg, dry_run=dry, notes=notes, now=now)
    except FileNotFoundError:
        print(f"[오류] {week}: plan.json 이 없습니다. `python -m studio plan init --week {week}` 부터 하세요.")
        return 1
    except ValidationError as e:
        print(f"[오류] {week}: plan.json 형식 오류 — `python -m studio plan validate --week {week}` ({e.error_count()}건)")
        return 1
    for n in notes:
        print(f"[알림] {n}")
    print_queue_counts(q, f"{week} 대기열{' (리허설)' if dry else ''}")

    media_ids = Q.media_needed(week, q.targets)
    for p in Q.prepare_media(week, media_ids, dry_run=dry):
        print(f"[경고] {p}")

    platforms = [p for p in Q.PRESCHEDULE_PLATFORMS if not args.platform or p in args.platform]
    outcomes: list[Q.Outcome] = []
    if platforms:
        outcomes = Q.run_week(week, cfg, now=now, platforms=platforms, preschedule=True, dry_run=dry,
                              queue=q if dry else None, media=False)
    elif dry:
        Q.save_dryrun(q)
    print_outcomes(outcomes)
    waiting = [p for p in Q.PLATFORM_KINDS if p not in Q.PRESCHEDULE_PLATFORMS]
    print(f"예약 공개를 받지 않는 플랫폼({', '.join(waiting)})은 pending 으로 두고 `publish due` 가 공개 시각에 올립니다.")
    if dry:
        print(f"[dry-run] queue.json 은 바꾸지 않았습니다 → {Q.dryrun_path(week)}")
    return print_summary(outcomes, f"{week} 예약 요약")


# ---------------------------------------------------------------- due

def cmd_due(args: argparse.Namespace) -> int:
    cfg = load_config()
    dry = _dry(args)
    now = parse_now(args.now)
    lead = timedelta(minutes=args.lead_minutes)
    if args.week:
        weeks = [_week(args)]
    else:
        weeks = Q.recent_weeks(now)
    used: Counter = Counter()
    outcomes: list[Q.Outcome] = []
    found = 0
    for week in weeks:
        has_queue = Q.queue_path(week).exists() or (dry and Q.dryrun_path(week).exists())
        if not has_queue:
            if args.week:
                print(f"[오류] {week}: 대기열이 없습니다. `python -m studio publish schedule --week {week}` 를 먼저 하세요.")
                return 1
            continue
        found += 1
        outcomes += Q.run_week(week, cfg, now=now, lead=lead, platforms=args.platform, dry_run=dry, used=used)
    if not found:
        print(f"처리할 대기열이 없습니다({', '.join(weeks)}).")
    print_outcomes(outcomes)
    if dry:
        print("[dry-run] queue.json 은 바꾸지 않았습니다 → out/weeks/<주차>/queue.dryrun.json")
    return print_summary(outcomes, f"due {fmt_time(now)} (앞당김 {args.lead_minutes}분)")


# ---------------------------------------------------------------- status

def cmd_status(args: argparse.Namespace) -> int:
    week = _week(args)
    real, dry_p = Q.queue_path(week), Q.dryrun_path(week)
    use_dry = dry_p.exists() and (_dry(args) or not real.exists())
    if not real.exists() and not use_dry:
        print(f"{week}: 대기열이 없습니다. `python -m studio publish schedule --week {week}` 를 먼저 하세요.")
        return 1
    if use_dry:
        q = read_model(dry_p, PublishQueue)
        print(f"[리허설 결과] {dry_p}")
    else:
        q = Q.load_queue(week)
    now = Q._now()
    print(f"{week} 발행 대기열 ({len(q.targets)}건, {fmt_time(now)} 기준)")
    print(f"{'공개 시각':<13} {'플랫폼':<9} {'ID':<14} {'종류':<9} {'상태':<10} {'실패':<4} 주소·메모")
    for t in sorted(q.targets, key=Q.sort_key):
        memo = t.url or t.remote_id or ""
        if t.last_error and t.status != "published":
            memo = t.last_error
        ra = Q.retry_at(t)
        if ra:
            memo += f" (재시도 {fmt_time(ra)} 이후)"
        print(f"{fmt_time(t.publish_at):<13} {t.platform:<9} {t.asset_id:<14} {t.asset_kind:<9} {t.status:<10} {t.attempts:<4} {_one_line(memo)}".rstrip())
    c = Counter(t.status for t in q.targets)
    print("상태: " + " · ".join(f"{s} {c[s]}" for s in STATUS_ORDER if c[s]))
    done = c["published"] + c["scheduled"]
    waiting = c["pending"] + c["uploading"] + c["processing"]
    print(f"요약: 성공 {done} · 대기 {waiting} · 건너뜀 {c['skipped']} · 실패 {c['failed']}")
    if c["failed"]:
        print(f"실패 항목은 원인을 고친 뒤 `python -m studio publish retry --week {week} --asset <ID> --platform <P>`"
              f"(또는 --all-failed) 로 되살립니다.")
    return 1 if c["failed"] else 0


# ---------------------------------------------------------------- retry

def _retry_one(t: PublishTarget, now: datetime, *, reupload: bool) -> str:
    if t.remote_id and not reupload:
        t.status = "processing"
        how = f"processing(원격 ID {t.remote_id} 상태를 다시 확인 — 새로 올리려면 --reupload)"
    else:
        t.status, t.remote_id, t.url = "pending", None, None
        how = "pending(다음 실행에서 다시 올림)"
    t.attempts, t.last_error, t.updated_at = 0, None, now
    return how


def cmd_retry(args: argparse.Namespace) -> int:
    week = _week(args)
    if not args.all_failed and not (args.asset and args.platform):
        print("[오류] --asset 과 --platform 을 함께 주거나 --all-failed 를 주세요.")
        return 1
    if not Q.queue_path(week).exists():
        print(f"[오류] {week}: 대기열이 없습니다.")
        return 1
    allowed = {"failed"} | ({"skipped", "uploading", "processing", "pending"} if args.force else set())
    now = Q._now()
    changed, refused = 0, 0
    with Q.editing(week) as q:
        if args.all_failed:
            targets = [t for t in q.targets if t.status == "failed" and (not args.platform or t.platform == args.platform)]
        else:
            t = q.find(args.platform, args.asset)
            if t is None:
                print(f"[오류] {week}: {args.platform}:{args.asset} 대상이 대기열에 없습니다(`publish status --week {week}` 로 확인).")
                return 1
            targets = [t]
        for t in targets:
            if t.status not in allowed:
                print(f"[거절] {t.asset_id} {t.platform}: 상태가 {t.status} 입니다. retry 는 failed 만 되살립니다(강제로 하려면 --force).")
                refused += 1
                continue
            how = _retry_one(t, now, reupload=args.reupload)
            print(f"[되살림] {t.asset_id} {t.platform} → {how}")
            changed += 1
    print(f"요약: 되살림 {changed} · 거절 {refused} · 실패 0")
    return 1 if refused or (not changed and not args.all_failed) else 0


# ---------------------------------------------------------------- mark

def normalize_remote(platform: str, kind: str, remote_id: str, url: str | None) -> tuple[str, str | None]:
    """수동 업로드 ID·주소 정리. 유튜브 주소를 붙여 넣어도 영상 ID 만 뽑는다."""
    rid = remote_id.strip()
    if platform == "youtube":
        m = YOUTUBE_ID_RE.search(rid)
        if m:
            url = url or (rid if rid.startswith("http") else None)
            rid = m.group(1)
        tpl = MARK_URLS.get((platform, kind))
        if not url and tpl:
            url = tpl.format(id=rid)
    elif rid.startswith(("http://", "https://")):
        url = url or rid
        m = re.search(r"/(\d+)/?(?:[?#]|$)", rid)
        if m:
            rid = m.group(1)
    return rid, url


def cmd_mark(args: argparse.Namespace) -> int:
    week = _week(args)
    platform, asset_id = args.platform, args.asset
    now = Q._now()
    with Q.editing(week) as q:
        t = q.find(platform, asset_id)
        if t is None:
            try:
                item = next((i for i in planning.load_plan(week).items if i.id == asset_id), None)
            except (FileNotFoundError, ValidationError):
                item = None
            if item is None:
                print(f"[오류] {week}: {asset_id} 가 대기열에도 plan.json 에도 없습니다.")
                return 1
            if item.kind not in Q.PLATFORM_KINDS.get(platform, ()):
                print(f"[오류] {platform} 는 {item.kind} 를 다루지 않습니다.")
                return 1
            t = PublishTarget(asset_id=asset_id, asset_kind=item.kind, platform=platform,
                              publish_at=Q.aware(item.publish_at) if item.publish_at else now)
            q.targets.append(t)
            q.targets.sort(key=Q.sort_key)
        prev = f"{t.status}" + (f", 원격 ID {t.remote_id}" if t.remote_id else "")
        rid, url = normalize_remote(platform, t.asset_kind, args.remote_id, args.url)
        if not rid:
            print("[오류] --remote-id 가 비어 있습니다.")
            return 1
        same = t.remote_id == rid
        t.status, t.remote_id, t.attempts, t.last_error, t.updated_at = args.status, rid, 0, None, now
        t.url = url or (t.url if same else None)
        print(f"[등록] {asset_id} {platform}: {prev} → {t.status}, 원격 ID {rid}" + (f", {t.url}" if t.url else ""))
    print("요약: 등록 1 · 실패 0")
    return 0


# ---------------------------------------------------------------- 등록

def _platform_arg(p: argparse.ArgumentParser, multiple: bool = True) -> None:
    if multiple:
        p.add_argument("--platform", action="append", choices=list(Q.PLATFORM_KINDS), help="이 플랫폼만(여러 번 지정 가능)")
    else:
        p.add_argument("--platform", choices=list(Q.PLATFORM_KINDS))


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "publish", "발행 대기열·업로드")

    s = g.add_parser("schedule", help="대기열 생성 + 예약 가능한 플랫폼(youtube·facebook) 즉시 예약")
    s.add_argument("--week", help="주차(예: 2026-W42). 없으면 이번 정기 실행 주차")
    _platform_arg(s)
    s.add_argument("--now", help=argparse.SUPPRESS)
    s.add_argument("--dry-run", action="store_true", help="요청을 보내지 않고 기록만(queue.json 은 그대로)")
    s.set_defaults(func=cmd_schedule)

    d = g.add_parser("due", help="공개 시각이 된 항목 발행·처리 중 항목 확인(정기 실행용)")
    w = d.add_mutually_exclusive_group()
    w.add_argument("--week", help="이 주차만")
    w.add_argument("--all-recent", action="store_true", help="최근 3주 대기열 모두(기본값)")
    d.add_argument("--now", help="기준 시각(ISO, 시간대 없으면 KST). 기본: 지금")
    d.add_argument("--lead-minutes", type=int, default=int(Q.DEFAULT_LEAD.total_seconds() // 60), help="공개 시각보다 이만큼 먼저 올림(기본 10)")
    _platform_arg(d)
    d.add_argument("--dry-run", action="store_true", help="요청을 보내지 않고 기록만(queue.json 은 그대로)")
    d.set_defaults(func=cmd_due)

    st = g.add_parser("status", help="발행 대기열 표")
    st.add_argument("--week")
    st.add_argument("--dry-run", action="store_true", help="리허설 결과(queue.dryrun.json)를 보기")
    st.set_defaults(func=cmd_status)

    r = g.add_parser("retry", help="failed 항목을 다시 대기(pending)로")
    r.add_argument("--week")
    r.add_argument("--asset", help="콘텐츠 ID(예: 2026W42-S03)")
    _platform_arg(r, multiple=False)
    r.add_argument("--all-failed", action="store_true", help="이 주차의 failed 전부(--platform 으로 좁힐 수 있음)")
    r.add_argument("--reupload", action="store_true", help="원격 ID 가 있어도 새로 올림(중복 업로드 주의)")
    r.add_argument("--force", action="store_true", help="failed 가 아닌 항목(skipped·uploading 등)도 되살림")
    r.set_defaults(func=cmd_retry)

    m = g.add_parser("mark", help="수동 업로드 결과 등록(유튜브 업로드 패키지 → 영상 ID 연결 등)")
    m.add_argument("--week")
    m.add_argument("--asset", required=True)
    m.add_argument("--platform", required=True, choices=list(Q.PLATFORM_KINDS))
    m.add_argument("--remote-id", required=True, help="플랫폼의 ID(유튜브는 영상 주소를 붙여 넣어도 됨)")
    m.add_argument("--url")
    m.add_argument("--status", default="published", choices=["published", "scheduled"])
    m.set_defaults(func=cmd_mark)

    _register_storage(sub, g)


def _register_storage(sub: argparse._SubParsersAction, g: argparse._SubParsersAction) -> None:
    """`publish upload-media` 는 studio.publish.storage 가 붙인다(studio.cli 목록에 없으므로 여기서 부른다)."""
    if "upload-media" in g.choices:
        return
    try:
        from studio.publish import storage
    except ModuleNotFoundError as e:
        if e.name in ("studio.publish.storage",):
            return
        raise
    if hasattr(storage, "register"):
        storage.register(sub)
