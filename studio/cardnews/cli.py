"""`python -m studio render cardnews --week 2026-W42 [--only 2026W42-C01]`"""

from __future__ import annotations

import argparse
import sys

from studio import paths
from studio.cliutil import group
from studio.config import load_config


def split_only(values: list[str] | None) -> list[str] | None:
    """--only A --only B 또는 --only A,B → [A, B]."""
    if not values:
        return None
    return [x.strip() for v in values for x in v.split(",") if x.strip()]


def cmd_cardnews(args: argparse.Namespace) -> int:
    from studio.cardnews.render import render_week

    cfg = load_config()
    week = args.week or paths.publishing_week()
    done, failed = render_week(week, cfg, split_only(args.only))
    for cid, msg in failed:
        print(f"[오류] {cid}: {msg}", file=sys.stderr)
    if not done and not failed:
        print(f"{week}: 렌더할 카드뉴스 원고가 없습니다 (content/weeks/{week}/cardnews/*.json).", file=sys.stderr)
        return 1
    print(f"{week}: 카드뉴스 {len(done)}개 렌더, 실패 {len(failed)}개")
    return 1 if failed else 0


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "render", "렌더링(영상·카드뉴스·블로그)")
    p = g.add_parser("cardnews", help="카드뉴스 슬라이드(1080x1350 PNG·JPEG) 렌더")
    p.add_argument("--week", help="주차(예: 2026-W42). 기본: 이번 정기 실행 주차")
    p.add_argument("--only", action="append", metavar="ID", help="이 ID 만 렌더(여러 번 또는 쉼표로)")
    p.set_defaults(func=cmd_cardnews)
