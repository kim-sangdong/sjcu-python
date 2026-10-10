"""`python -m studio analytics <하위명령>`.

  analytics collect --week W [--sources youtube,instagram,facebook,tiktok,funnel] [--fake] [--dry-run] [--force] [--max-age-hours H]
      종료 코드: 0 모두 성공 · 2 일부 원천 실패(파일은 씀) · 1 모두 실패 또는 실행 불가
  analytics report  --week W            → reports/W/channel-revenue.md, .csv
  analytics ad-data --week W [--budget N] → reports/W/ad-decision-data.json
--week 를 빼면 지금이 속한 ISO 주(발행 중인 주)를 쓴다.
"""

from __future__ import annotations

import argparse
import sys

from studio import paths
from studio.analytics.common import ALL_SOURCES, SOURCE_LABEL, current_week, env_dry_run
from studio.cliutil import group


def _week(args: argparse.Namespace) -> str:
    week = args.week or current_week()
    paths.week_monday(week)   # 형식 확인
    return week


def _sources(value: str | None) -> list[str]:
    if not value:
        return list(ALL_SOURCES)
    out = [s.strip().lower() for s in value.split(",") if s.strip()]
    bad = [s for s in out if s not in ALL_SOURCES]
    if bad:
        raise ValueError(f"모르는 원천: {', '.join(bad)} (가능: {','.join(ALL_SOURCES)})")
    return out


def cmd_collect(args: argparse.Namespace) -> int:
    from studio.analytics.collect import CollectRefused, is_fresh, run

    try:
        week = _week(args)
        sources = _sources(args.sources)
    except ValueError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    dry = bool(args.dry_run or env_dry_run())
    if args.max_age_hours is not None and not dry and is_fresh(week, sources, args.max_age_hours, fake=args.fake):
        print(f"{week}: {args.max_age_hours}시간 안에 수집한 metrics.json 이 있어 다시 수집하지 않습니다.")
        return 0
    try:
        metrics, path = run(week, sources=sources, fake=args.fake, dry_run=dry, force=args.force)
    except (CollectRefused, ValueError, FileNotFoundError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    ok, failed = [], []
    for s in sources:
        res = metrics["sources"][s]
        label = SOURCE_LABEL.get(s, s)
        if res.get("last_error") and res.get("last_attempt_at") and not res.get("dry_run"):
            failed.append(s)
            print(f"[실패] {label}: {res['last_error']} — 이전 수집본({res.get('collected_at')})을 유지합니다.")
        elif res.get("ok"):
            ok.append(s)
            n = len(res.get("rows") or []) if s != "funnel" else sum(len(v) for v in (res.get("data") or {}).values() if isinstance(v, list))
            tag = "꺼짐" if res.get("skipped") else ("dry-run" if res.get("dry_run") else ("일부" if res.get("partial") else "성공"))
            print(f"[{tag}] {label}: {n}행 ({res.get('seconds')}초)")
            for w in res.get("warnings") or []:
                print(f"    - {w}")
        else:
            failed.append(s)
            print(f"[실패] {label}: {res.get('error')}")
    if dry:
        for line in metrics.get("requests") or []:
            print(f"  [dry-run] {line}")
    if args.fake:
        print("※ 합성(--fake) 데이터입니다. 실제 성과가 아닙니다.")
    print(f"저장: {path}")
    if failed and not ok:
        return 1
    return 2 if failed else 0


def cmd_report(args: argparse.Namespace) -> int:
    from studio.analytics.report import f_int, write_report

    try:
        week = _week(args)
        md, csv_path, s = write_report(week)
    except (FileNotFoundError, ValueError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    k = s["kpi"]

    def v(key: str, unit: str = "") -> str:
        return "결손" if k[key] is None else f"{f_int(k[key])}{unit}"

    print(f"{week} 조회 {v('views')} · 클릭 {v('clicks')} · 설치 {v('installs')} · 유료 전환 {v('paid')} · 매출 {v('revenue_krw', '원')}")
    for w in s["warnings"]:
        print(f"[결손] {w}")
    print(f"저장: {md}\n저장: {csv_path}")
    return 0


def cmd_ad_data(args: argparse.Namespace) -> int:
    from studio.analytics.addecision import write_ad_data

    try:
        week = _week(args)
        path, data = write_ad_data(week, budget_krw=args.budget)
    except (FileNotFoundError, ValueError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    sm = data["summary"]
    print(f"{week} 콘텐츠×플랫폼 {sm['items']}개 — boost {sm['boost']} · hold {sm['hold']} · stop {sm['stop']} · "
          f"표본 부족 {sm['insufficient_data']} (예산 {data['weekly_budget_krw']:,.0f}원)")
    for w in data["data_warnings"]:
        print(f"[결손] {w}")
    print(f"저장: {path}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "analytics", "성과 수집·매출 귀속·광고 판단 데이터")
    c = g.add_parser("collect", help="플랫폼 지표·클릭·설치·결제 수집 → reports/<week>/metrics.json")
    c.add_argument("--week", help="ISO 주차(예: 2026-W42). 없으면 이번 주")
    c.add_argument("--sources", help=f"쉼표로 구분한 원천(기본 전부: {','.join(ALL_SOURCES)})")
    c.add_argument("--fake", action="store_true", help="합성 데이터(형식 확인·리허설용)")
    c.add_argument("--dry-run", action="store_true", help="요청을 보내지 않고 보낼 요청만 기록(STUDIO_DRY_RUN=1 과 같음)")
    c.add_argument("--force", action="store_true", help="실제 수집본 위에 합성 데이터를 덮어쓰기")
    c.add_argument("--max-age-hours", type=float, help="이 시간 안에 수집한 결과가 있으면 다시 수집하지 않음")
    c.set_defaults(func=cmd_collect)
    r = g.add_parser("report", help="주간 매출 보고 → reports/<week>/channel-revenue.md·.csv")
    r.add_argument("--week")
    r.set_defaults(func=cmd_report)
    a = g.add_parser("ad-data", help="광고 판단 데이터 → reports/<week>/ad-decision-data.json")
    a.add_argument("--week")
    a.add_argument("--budget", type=float, help="이번 주 광고 예산(원). 없으면 channel.yaml ads.weekly_budget_krw")
    a.set_defaults(func=cmd_ad_data)
