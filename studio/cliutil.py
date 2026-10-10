"""여러 모듈이 같은 명령 묶음(render, auth, publish …)에 하위 명령을 붙일 때 쓰는 도우미.

    from studio.cliutil import group
    def register(sub):
        g = group(sub, "render", "렌더링")
        p = g.add_parser("video", help="영상 렌더")
        p.set_defaults(func=cmd_video)
"""

from __future__ import annotations

import argparse

_ATTR = "_studio_groups"
# 조회용이 아니라 기록용(테스트가 id(sub) 로 묶음을 들여다볼 때 쓴다). 조회는 sub 객체 속성으로만 한다.
_GROUPS: dict[int, dict[str, argparse._SubParsersAction]] = {}


def group(sub: argparse._SubParsersAction, name: str, help: str | None = None) -> argparse._SubParsersAction:
    # 묶음은 subparsers 객체 자신에 붙여 둔다(파서를 여러 번 만들어도 id 재사용으로 섞이지 않게).
    groups: dict[str, argparse._SubParsersAction] = sub.__dict__.setdefault(_ATTR, {})
    _GROUPS[id(sub)] = groups
    if name not in groups:
        p = sub.add_parser(name, help=help)
        groups[name] = p.add_subparsers(dest=f"{name}_cmd", metavar="<하위명령>")
    return groups[name]
