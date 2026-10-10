"""주간 파이프라인 — /weekly-publish 가 부르는 단계 묶음.

  python -m studio weekly prepare [--week W]          계획 뼈대·기획 입력 자료 만들기
  python -m studio weekly build   [--week W] [--tts silent] [--only ID ...]
        원고 검증 → 독창성(원고) → 렌더(영상·카드뉴스·블로그) → 독창성(렌더)
        차단이 있으면 종료 코드 2 와 함께 고칠 목록(originality.md)을 알려 준다.
  python -m studio weekly ship    [--week W] [--dry-run]
        미디어 업로드 → 발행 대기열 생성·예약 → (실제 발행만) 아카이브 등록 → 쓴 문항 기록(bank mark-used)
        마지막 build 가 끝까지 통과하지 않았거나, 그 뒤에 원고를 고쳤으면 멈춘다(종료 코드 2, --force 로 무시).
  python -m studio weekly status  [--week W]

각 단계는 다른 모듈의 CLI 를 같은 파이썬으로 호출한다(모듈 사이 결합을 CLI 계약으로 한정).
진행 기록은 content/weeks/<week>/pipeline.json 에 남아, 실패한 곳부터 다시 돌릴 수 있다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime

from studio import paths
from studio.cliutil import group
from studio.models import read_json, write_json


def _state_path(week: str):
    return paths.content_dir(week) / "pipeline.json"


def _record(week: str, step: str, code: int, seconds: float, note: str = "") -> None:
    state = read_json(_state_path(week), default={}) or {}
    state.setdefault("week", week)
    state.setdefault("steps", {})[step] = {
        "exit_code": code,
        "ok": code == 0,
        "seconds": round(seconds, 1),
        "at": datetime.now(paths.KST).isoformat(timespec="seconds"),
        "note": note,
    }
    write_json(_state_path(week), state)


def run_step(week: str, step: str, argv: list[str], *, ok_codes=(0,), env: dict | None = None) -> int:
    print(f"\n=== [{step}] python -m studio {' '.join(argv)}", flush=True)
    t0 = time.time()
    proc = subprocess.run([sys.executable, "-m", "studio", *argv], env={**os.environ, **(env or {})})
    _record(week, step, proc.returncode, time.time() - t0)
    if proc.returncode not in ok_codes:
        print(f"=== [{step}] 실패 (종료 코드 {proc.returncode})", flush=True)
    return proc.returncode


def _week(args) -> str:
    return args.week or paths.publishing_week()


def _only(args) -> list[str]:
    return [x for i in (args.only or []) for x in ("--only", i)]


def cmd_prepare(args: argparse.Namespace) -> int:
    week = _week(args)
    return run_step(week, "prepare", ["plan", "init", "--week", week])


BUILD_STEPS = ("validate", "originality-script", "render-video", "render-cardnews", "render-blog", "originality-render")


def _reset_steps(week: str, steps: tuple[str, ...]) -> None:
    """다시 돌릴 단계의 지난 기록을 지운다 — 이번 실행이 중간에 실패해도 지난번 '통과' 기록이 남아 ship 을 열어 주지 않게."""
    state = read_json(_state_path(week), default=None)
    if not state or not state.get("steps"):
        return
    for st in steps:
        state["steps"].pop(st, None)
    state.pop("build_inputs", None)
    write_json(_state_path(week), state)


def _content_files(week: str) -> list:
    d = paths.content_dir(week)
    files = [d / "plan.json"]
    for sub in ("longform", "shorts", "cardnews", "blog"):
        files += sorted((d / sub).glob("*.json"))
    return [f for f in files if f.exists()]


def content_hashes(week: str) -> dict[str, str]:
    """원고 파일(plan.json + 원고 JSON)의 내용 해시 — 수정 시각이 아니라 내용으로 비교한다(CI 체크아웃은 시각을 바꾼다)."""
    base = paths.content_dir(week)
    return {f.relative_to(base).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()[:16] for f in _content_files(week)}


def stale_since_check(week: str) -> list[str]:
    """마지막으로 통과한 build 뒤에 바뀐(또는 새로 생긴·지운) 원고 — 렌더·독창성 검사를 다시 거치지 않았다."""
    state = read_json(_state_path(week), default={}) or {}
    built = state.get("build_inputs")
    if not isinstance(built, dict):
        return []
    now = content_hashes(week)
    return sorted(k for k in set(built) | set(now) if built.get(k) != now.get(k))


def cmd_build(args: argparse.Namespace) -> int:
    week = _week(args)
    env = {"STUDIO_TTS": args.tts} if args.tts else None
    _reset_steps(week, BUILD_STEPS)
    if run_step(week, "validate", ["plan", "validate", "--week", week]) != 0:
        print("원고 검증 실패 — 위 [오류] 를 고친 뒤 다시 실행하세요.")
        return 1
    code = run_step(week, "originality-script", ["check", "originality", "--week", week, "--stage", "script"], ok_codes=(0, 2))
    if code == 2:
        print(f"독창성 차단(원고 단계) — {paths.content_dir(week) / 'originality.md'} 의 수정 지시대로 원고를 고친 뒤 다시 실행하세요.")
        return 2
    if code != 0:
        return code
    tts = ["--tts", args.tts] if args.tts else []
    for step, argv in (
        ("render-video", ["render", "video", "--week", week, *tts, *_only(args)]),
        ("render-cardnews", ["render", "cardnews", "--week", week, *_only(args)]),
        ("render-blog", ["render", "blog", "--week", week, *_only(args)]),
    ):
        if run_step(week, step, argv, env=env) != 0:
            return 1
    code = run_step(week, "originality-render", ["check", "originality", "--week", week, "--stage", "render"], ok_codes=(0, 2))
    if code == 2:
        print(f"독창성 차단(렌더 단계) — {paths.content_dir(week) / 'originality.md'} 를 보고 해당 원고를 고쳐 build 를 다시 실행하세요.")
    if code == 0:
        state = read_json(_state_path(week), default={}) or {}
        state["build_inputs"] = content_hashes(week)       # ship 이 '검사 뒤에 고친 원고'를 찾는 기준
        write_json(_state_path(week), state)
    return code


def cmd_ship(args: argparse.Namespace) -> int:
    week = _week(args)
    dry = ["--dry-run"] if args.dry_run else []
    state = read_json(_state_path(week), default={}) or {}
    last = state.get("steps", {}).get("originality-render", {})
    if not last.get("ok") and not args.force:
        print("렌더 단계 독창성 검사를 통과하지 않았습니다. `weekly build` 를 먼저 끝내세요(--force 로 무시 가능).")
        return 2
    stale = stale_since_check(week)
    if stale and not args.force:
        print("마지막 build(렌더·독창성 검사) 뒤에 고친 원고가 있습니다: " + ", ".join(stale)
              + " — 고친 원고는 렌더·검사를 다시 거쳐야 합니다. `weekly build` 를 다시 돌리세요(--force 로 무시 가능).")
        return 2
    if run_step(week, "upload-media", ["publish", "upload-media", "--week", week, *dry]) != 0:
        return 1
    if run_step(week, "schedule", ["publish", "schedule", "--week", week, *dry]) != 0:
        return 1
    if not args.dry_run:
        run_step(week, "archive", ["archive", "add", "--week", week])
        mark_used(week)
    return run_step(week, "status", ["publish", "status", "--week", week])


def mark_used(week: str) -> int:
    """원고 장면·슬라이드의 params.bank_id 를 콘텐츠별로 모아 `bank mark-used` 로 기록한다(실제 발행 때만).

    예전에는 스킬 문서가 사람(에이전트)에게 문항마다 손으로 부르게 했다 — 빠뜨리면 앱 은행의 '콘텐츠 사용' 기록과
    다음 주 '재사용 가능 수'가 틀어진다. 실패한 콘텐츠가 있어도 나머지는 계속하고, 실패 수를 돌려준다.
    """
    from studio import planning

    try:
        plan = planning.load_plan(week)
    except Exception as e:  # noqa: BLE001
        print(f"[mark-used] 계획을 읽지 못해 건너뜀: {e}")
        return 1
    failed = 0
    for item in plan.items:
        p = planning.asset_path(week, item.kind, item.id)
        if not p.exists() or item.kind == "blog":
            continue
        try:
            obj = planning.read_model(p, planning.KIND_MODEL[item.kind])
        except Exception as e:  # noqa: BLE001
            print(f"[mark-used] {item.id}: 원고를 읽지 못함 — {e}")
            failed += 1
            continue
        ids: list[str] = []
        for _, _, _, raw in planning.bank_refs(obj):
            ids += planning.bank_ids(raw) or []
        ids = list(dict.fromkeys(ids))
        if ids and run_step(week, f"mark-used {item.id}", ["bank", "mark-used", "--asset", item.id, "--items", *ids]) != 0:
            failed += 1
    return failed


def cmd_status(args: argparse.Namespace) -> int:
    week = _week(args)
    state = read_json(_state_path(week), default={}) or {}
    for step, s in (state.get("steps") or {}).items():
        mark = "O" if s.get("ok") else "X"
        print(f"[{mark}] {step:<20} 코드 {s.get('exit_code')}  {s.get('seconds')}초  {s.get('at')}")
    return subprocess.run([sys.executable, "-m", "studio", "plan", "status", "--week", week]).returncode


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "weekly", "주간 파이프라인(/weekly-publish 가 사용)")
    p = g.add_parser("prepare", help="계획 뼈대·기획 입력 자료")
    p.add_argument("--week")
    p.set_defaults(func=cmd_prepare)
    b = g.add_parser("build", help="검증→독창성→렌더→독창성")
    b.add_argument("--week")
    b.add_argument("--tts", choices=["google", "edge", "silent"], help="TTS 공급자 강제(리허설은 silent)")
    b.add_argument("--only", action="append", help="특정 콘텐츠 ID 만 렌더(여러 번 지정 가능)")
    b.set_defaults(func=cmd_build)
    s = g.add_parser("ship", help="업로드→예약→아카이브")
    s.add_argument("--week")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_ship)
    st = g.add_parser("status", help="파이프라인 진행 기록")
    st.add_argument("--week")
    st.set_defaults(func=cmd_status)
