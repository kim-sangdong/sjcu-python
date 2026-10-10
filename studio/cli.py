"""`python -m studio <명령>` 진입점.

각 하위 모듈은 `register(subparsers)` 함수를 내보내고, 자기 명령을 붙인다.
같은 묶음(render, auth, publish …)에 여러 모듈이 붙을 때는 studio.cliutil.group 을 쓴다.
명령 처리 함수는 argparse 의 `func` 기본값으로 지정하며 종료 코드(int)를 돌려준다.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from studio.config import MissingSecret

COMMAND_MODULES = [
    "studio.setup_cmd",        # setup fonts | doctor
    "studio.planning",         # plan init | validate | status
    "studio.video.cli",        # render video ...
    "studio.cardnews.cli",     # render cardnews ...
    "studio.blog.cli",         # render blog ...
    "studio.originality.cli",  # check originality | archive add
    "studio.publish.cli",      # publish schedule | due | ...
    "studio.publish.auth_cli", # auth youtube | instagram | tiktok | tistory
    "studio.comments.cli",     # comments fetch | post
    "studio.analytics.cli",    # analytics collect | report
    "studio.pipeline",         # weekly prepare | finalize | status
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description="콘텐츠→앱 결제 자동화 도구")
    sub = parser.add_subparsers(dest="command", metavar="<명령>")
    for name in COMMAND_MODULES:
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name and (e.name == name or name.startswith(e.name + ".")):
                continue  # 아직 없는 모듈은 건너뛴다
            raise
        mod.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except MissingSecret as e:
        print(f"[설정 필요] {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
