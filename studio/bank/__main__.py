"""`python -m studio.bank <하위명령>` — studio/cli.py 에 bank 가 등록되기 전에도 쓸 수 있는 진입점.

등록 뒤에는 `python -m studio bank <하위명령>` 과 똑같이 동작한다.
"""

from __future__ import annotations

import argparse
import sys

from studio.config import MissingSecret


def main(argv: list[str] | None = None) -> int:
    from studio.bank.cli import register

    parser = argparse.ArgumentParser(prog="studio.bank", description="문제은행 명령")
    sub = parser.add_subparsers(dest="command", metavar="<명령>")
    register(sub)
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(["bank", *raw] if raw[:1] != ["bank"] else raw)
    if not getattr(args, "func", None):
        parser.parse_args(["bank", "--help"])
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
