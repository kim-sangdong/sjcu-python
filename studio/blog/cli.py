"""`python -m studio render blog --week 2026-W42 [--only 2026W42-B01]`"""

from __future__ import annotations

import argparse
import sys

from studio import paths
from studio.cliutil import group
from studio.config import load_config


def cmd_blog(args: argparse.Namespace) -> int:
    from studio.blog.render import render_week
    from studio.cardnews.cli import split_only

    cfg = load_config()
    week = args.week or paths.publishing_week()
    done, failed = render_week(week, cfg, split_only(args.only))
    for pid, msg in failed:
        print(f"[오류] {pid}: {msg}", file=sys.stderr)
    if not done and not failed:
        print(f"{week}: 렌더할 블로그 원고가 없습니다 (content/weeks/{week}/blog/*.json).", file=sys.stderr)
        return 1
    print(f"{week}: 블로그 {len(done)}편 렌더, 실패 {len(failed)}편")
    return 1 if failed else 0


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "render", "렌더링(영상·카드뉴스·블로그)")
    p = g.add_parser("blog", help="블로그 글 HTML(티스토리 HTML 모드용)·대표 이미지 렌더")
    p.add_argument("--week", help="주차(예: 2026-W42). 기본: 이번 정기 실행 주차")
    p.add_argument("--only", action="append", metavar="ID", help="이 ID 만 렌더(여러 번 또는 쉼표로)")
    p.set_defaults(func=cmd_blog)
