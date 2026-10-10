"""독창성 검사·아카이브 명령.

  python -m studio check originality --week 2026-W42 --stage script|render [--only ID ...]
      종료 코드: 0 통과·경고, 2 차단, 1 실행 오류(원고 형식 오류·아카이브 손상 등)
  python -m studio archive add --week 2026-W42 [--only ID ...] [--published-only] [--include-blocked]
      발행한 자산의 지문을 archive/index.json 에 넣는다(같은 asset_id 는 교체).
  python -m studio archive list [--week W] [--kind short]
"""

from __future__ import annotations

import argparse
import sys
import time

from studio import paths
from studio.cliutil import group
from studio.config import load_config
from studio.manifest import load_manifest
from studio.models import OriginalityReport, PublishQueue, read_model

KO = {"pass": "통과", "warn": "경고", "block": "차단"}


def _ids(values: list[str] | None) -> list[str]:
    return [x.strip() for v in (values or []) for x in v.split(",") if x.strip()]


def cmd_check(args: argparse.Namespace) -> int:
    from studio.originality.archive import ArchiveError
    from studio.originality.checker import CheckError, report_paths, run_check

    week = args.week or paths.publishing_week()
    try:
        report, ck, elapsed = run_check(week, args.stage, only=_ids(args.only) or None, cfg=load_config())
    except (CheckError, ArchiveError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    for a in report.assets:
        if a.verdict == "pass" and not args.verbose:
            continue
        print(f"[{KO[a.verdict]}] {a.asset_id}")
        for f in a.fixes[:6]:
            print(f"    - {f}")
    n = {k: sum(1 for a in report.assets if a.verdict == k) for k in KO}
    json_p, md_p = report_paths(week)
    print(f"{week} {args.stage}: {KO[report.verdict]} — 차단 {n['block']} · 경고 {n['warn']} · 통과 {n['pass']} ({elapsed:.1f}초)")
    print(f"보고서: {md_p.relative_to(paths.ROOT) if md_p.is_relative_to(paths.ROOT) else md_p}")
    return 2 if report.verdict == "block" else 0


def _published_ids(week: str) -> set[str] | None:
    p = paths.content_dir(week) / "queue.json"
    if not p.exists():
        return None
    q = read_model(p, PublishQueue)
    return {t.asset_id for t in q.targets if t.status in ("published", "scheduled", "processing")}


def cmd_archive_add(args: argparse.Namespace) -> int:
    from studio.originality import archive as A
    from studio.originality.checker import CheckError, Checker
    from studio.originality.features import visual_fx

    t0 = time.time()
    week = args.week or paths.publishing_week()
    cfg = load_config()
    try:
        ck = Checker(week, "script", cfg)
    except (CheckError, A.ArchiveError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    only = set(_ids(args.only))
    docs = [d for d in ck.docs if not only or d.asset_id in only]
    if only - {d.asset_id for d in docs}:
        print(f"[오류] 원고를 찾지 못한 ID: {', '.join(sorted(only - {d.asset_id for d in docs}))}", file=sys.stderr)
        return 1
    verdicts: dict[str, str] = {}
    rp = paths.content_dir(week) / "originality.json"
    if rp.exists():
        try:
            verdicts = {a.asset_id: a.verdict for a in read_model(rp, OriginalityReport).assets}
        except ValueError:
            pass
    published = _published_ids(week) if args.published_only else None
    if args.published_only and published is None:
        print(f"[오류] --published-only 인데 {week} 의 queue.json 이 없습니다. 발행 예약(`publish schedule`)을 먼저 하세요.", file=sys.stderr)
        return 1
    manifest = load_manifest(week)
    entries, skipped = [], []
    for d in docs:
        if verdicts.get(d.asset_id) == "block" and not args.include_blocked:
            skipped.append(f"{d.asset_id}(독창성 차단)")
            continue
        if published is not None and d.asset_id not in published:
            skipped.append(f"{d.asset_id}(발행 기록 없음)")
            continue
        asset = manifest.get(d.asset_id)
        vis = visual_fx(d, asset, ck.th, freeze=False) if asset else None
        if vis and vis.error:
            print(f"[경고] {d.asset_id}: 렌더 파일을 읽지 못해 시각 지문 없이 넣습니다 — {vis.error[:200]}", file=sys.stderr)
        elif not asset:
            print(f"[경고] {d.asset_id}: 렌더 기록(manifest)이 없어 텍스트 지문만 넣습니다.", file=sys.stderr)
        entries.append(A.entry_from(d, ck.tx[d.asset_id], vis, ck.th))
    try:
        with A.editing() as idx:
            for e in entries:
                A.upsert(idx, e)
            total = len(idx["assets"])
    except A.ArchiveError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    for s in skipped:
        print(f"[건너뜀] {s}")
    size = paths.archive_index().stat().st_size
    print(f"아카이브 등록 {len(entries)}개 (전체 {total}개, {size / 1024:.0f}KB, {time.time() - t0:.1f}초): {paths.archive_index()}")
    return 0


def cmd_archive_list(args: argparse.Namespace) -> int:
    from studio.originality.archive import ArchiveError, load_index

    try:
        idx = load_index()
    except ArchiveError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    rows = [a for a in idx.get("assets", []) if (not args.week or a.get("week") == args.week) and (not args.kind or a.get("kind") == args.kind)]
    for a in rows[-args.limit:]:
        fmt = a.get("format") or ""
        print(f"{a.get('asset_id', ''):<14} {a.get('kind', ''):<9} {fmt:<10} {a.get('title', '')}")
    print(f"{len(rows)}개")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "check", "검사")
    p = g.add_parser("originality", help="독창성(반복·대량생산) 검사 — 차단이면 종료 코드 2")
    p.add_argument("--week")
    p.add_argument("--stage", choices=["script", "render"], default="script", help="script: 원고만 / render: 렌더 파일까지")
    p.add_argument("--only", action="append", help="이 ID 만 판정(여러 번 또는 쉼표). 비교 대상은 그대로 주 전체")
    p.add_argument("-v", "--verbose", action="store_true", help="통과한 자산도 출력")
    p.set_defaults(func=cmd_check)

    a = group(sub, "archive", "발행 지문 아카이브(archive/index.json)")
    x = a.add_parser("add", help="이번 주 자산 지문을 아카이브에 추가(같은 ID 는 교체)")
    x.add_argument("--week")
    x.add_argument("--only", action="append", help="이 ID 만(여러 번 또는 쉼표)")
    x.add_argument("--published-only", action="store_true", help="queue.json 에서 published·scheduled 인 자산만")
    x.add_argument("--include-blocked", action="store_true", help="독창성 차단 자산도 넣기(기본은 건너뜀)")
    x.set_defaults(func=cmd_archive_add)
    ls = a.add_parser("list", help="아카이브 목록")
    ls.add_argument("--week")
    ls.add_argument("--kind", choices=["longform", "short", "cardnews", "blog"])
    ls.add_argument("--limit", type=int, default=200)
    ls.set_defaults(func=cmd_archive_list)
