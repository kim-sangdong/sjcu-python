"""여러 모듈이 같은 명령 묶음(render, auth, publish …)에 하위 명령을 붙일 때 쓰는 도우미.

    from studio.cliutil import group
    def register(sub):
        g = group(sub, "render", "렌더링")
        p = g.add_parser("video", help="영상 렌더")
        p.set_defaults(func=cmd_video)
"""

from __future__ import annotations

import argparse

_GROUPS: dict[int, dict[str, argparse._SubParsersAction]] = {}


def group(sub: argparse._SubParsersAction, name: str, help: str | None = None) -> argparse._SubParsersAction:
    groups = _GROUPS.setdefault(id(sub), {})
    if name not in groups:
        p = sub.add_parser(name, help=help)
        groups[name] = p.add_subparsers(dest=f"{name}_cmd", metavar="<하위명령>")
    return groups[name]
