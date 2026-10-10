"""`python -m studio auth <플랫폼>` — 각 플랫폼 모듈의 register_auth(group) 를 모아 붙인다."""

from __future__ import annotations

import argparse
import importlib

from studio.cliutil import group

AUTH_MODULES = [
    "studio.publish.google_auth",   # auth youtube
    "studio.publish.meta_auth",     # auth instagram | facebook
    "studio.publish.tiktok",        # auth tiktok
    "studio.publish.tistory",       # auth tistory
]


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "auth", "플랫폼 로그인·토큰 발급")
    for name in AUTH_MODULES:
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name and (e.name == name or name.startswith(e.name + ".")):
                continue
            raise
        if hasattr(mod, "register_auth"):
            mod.register_auth(g)
