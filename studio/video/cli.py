"""영상 렌더 명령.

  python -m studio render video --week 2026-W42 [--only ID ...] [--kind longform|short] [--tts silent]
                                [--jobs N] [--preview] [--keep-work] [--dry-run]

원고: content/weeks/<week>/longform/<id>.json, shorts/<id>.json (VideoScript)
결과: out/weeks/<week>/<id>/video.mp4, thumbnail.png|cover.png, subtitles.srt, timeline.json (+ manifest 기록)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from studio import paths
from studio.cliutil import group
from studio.config import MissingSecret, load_config
from studio.manifest import upsert_asset
from studio.models import VideoScript, read_model

KIND_DIRS = {"longform": "longform", "short": "shorts"}


def find_scripts(week: str, only: list[str] | None = None, kind: str | None = None) -> list[Path]:
    out: list[Path] = []
    for k, sub in KIND_DIRS.items():
        if kind and kind != k:
            continue
        out += sorted((paths.content_dir(week) / sub).glob("*.json"))
    if only:
        want = set(only)
        out = [p for p in out if p.stem in want]
    return out


def is_dry_run(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False)) or os.environ.get("STUDIO_DRY_RUN", "").strip() in ("1", "true", "yes")


def cmd_video(args: argparse.Namespace) -> int:
    from studio.video.compose import RenderOptions, render_video
    from studio.video.scenes import SceneError

    cfg = load_config()
    week = args.week or paths.publishing_week()
    scripts = find_scripts(week, args.only, args.kind)
    if not scripts:
        where = paths.content_dir(week)
        print(f"{week}: 렌더할 영상 원고가 없습니다 ({where}/longform, shorts). --only/--kind 조건도 확인하세요.", file=sys.stderr)
        return 2
    if args.only:
        missing = set(args.only) - {p.stem for p in scripts}
        for m in sorted(missing):
            print(f"[경고] {m}: 원고 파일을 찾지 못했습니다.", file=sys.stderr)
    opts = RenderOptions(tts=args.tts, dry_run=is_dry_run(args), jobs=args.jobs, keep_work=args.keep_work, preview=args.preview)
    failed = 0
    t_all = time.time()
    for p in scripts:
        try:
            script = read_model(p, VideoScript)
        except ValidationError as e:
            err = e.errors()[0]
            print(f"[오류] {p.name}: 원고 형식 오류 — {err['loc']} {err['msg']} (총 {e.error_count()}건)", file=sys.stderr)
            failed += 1
            continue
        if script.id != p.stem or script.week != week:
            print(f"[경고] {p.name}: 파일 안의 id/week({script.id}, {script.week})가 파일 이름·주차와 다릅니다.", file=sys.stderr)
        print(f"▶ {script.id} ({script.format}) 렌더 시작")
        try:
            res = render_video(script, cfg, opts)
        except MissingSecret:
            raise
        except SceneError as e:
            failed += 1
            print(f"[오류] {script.id}: 원고의 장면 설정을 고쳐 주세요 — {e}", file=sys.stderr)
            continue
        except Exception as e:  # 한 편이 실패해도 나머지는 계속
            failed += 1
            print(f"[오류] {script.id}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        upsert_asset(week, res.asset)
        for w in res.warnings:
            print(f"  [경고] {w}")
        a = res.asset
        print(f"✔ {script.id}: {a.duration_seconds:.1f}초, 장면 {a.meta['scene_count']}개, 정지 비율 {a.meta['static_ratio']:.0%}, "
              f"TTS {a.meta['tts_provider']} — {res.seconds:.0f}초 걸림 → {a.files['video']}")
    print(f"{week}: 영상 {len(scripts) - failed}/{len(scripts)}편 완료 ({time.time() - t_all:.0f}초)")
    return 1 if failed else 0


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "render", "렌더링(영상·카드뉴스·블로그)")
    p = g.add_parser("video", help="영상 렌더(TTS → 장면 → mp4 + 썸네일·자막)")
    p.add_argument("--week", help="주차(예: 2026-W42). 없으면 이번 정기 실행 주차")
    p.add_argument("--only", nargs="+", metavar="ID", help="이 ID 들만 렌더")
    p.add_argument("--kind", choices=["longform", "short"], help="종류만 골라 렌더")
    p.add_argument("--tts", choices=["google", "edge", "silent"], help="TTS 공급자 강제(silent = 무음 리허설)")
    p.add_argument("--jobs", type=int, help="동시에 인코딩할 정지 세그먼트 수(기본: CPU 수 ÷ 2)")
    p.add_argument("--preview", action="store_true", help="장면별 미리보기 PNG 저장(<id>/preview/)")
    p.add_argument("--keep-work", action="store_true", help="중간 파일(_work/) 남기기")
    p.add_argument("--dry-run", action="store_true", help="TTS 요청을 보내지 않고 무음으로 렌더")
    p.set_defaults(func=cmd_video)
