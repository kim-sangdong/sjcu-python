"""설치 보조 명령.

  python -m studio setup fonts     한글 폰트(나눔고딕, OFL) 내려받기
  python -m studio doctor          설정·비밀값·도구 점검
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

from studio import paths
from studio.config import load_config, load_env

FONT_FILES = ["NanumGothic.ttf", "NanumGothicBold.ttf", "NanumGothicExtraBold.ttf", "NanumGothicLight.ttf"]
# 나눔고딕은 SIL Open Font License 1.1 — 상업적 이용·영상 삽입 가능.
GOOGLE_FONTS_RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/{name}"
GOOGLE_FONTS_NAMES = {
    "NanumGothic.ttf": "NanumGothic-Regular.ttf",
    "NanumGothicBold.ttf": "NanumGothic-Bold.ttf",
    "NanumGothicExtraBold.ttf": "NanumGothic-ExtraBold.ttf",
}


def _from_google_fonts(dest: Path) -> bool:
    ok = True
    for local, remote in GOOGLE_FONTS_NAMES.items():
        if (dest / local).exists():
            continue
        try:
            r = requests.get(GOOGLE_FONTS_RAW.format(name=remote), timeout=60)
            r.raise_for_status()
            (dest / local).write_bytes(r.content)
        except requests.RequestException:
            ok = False
    return ok


def _from_pypi(dest: Path) -> bool:
    """대체 경로: 나눔고딕을 포함한 PyPI 패키지(koreanize-matplotlib) 휠에서 꺼낸다."""
    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "download", "--no-deps", "-q", "-d", tmp, "koreanize-matplotlib"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return False
        for whl in Path(tmp).glob("*.whl"):
            with zipfile.ZipFile(io.BytesIO(whl.read_bytes())) as z:
                for n in z.namelist():
                    name = n.rsplit("/", 1)[-1]
                    if name in FONT_FILES and not (dest / name).exists():
                        (dest / name).write_bytes(z.read(n))
    return all((dest / f).exists() for f in GOOGLE_FONTS_NAMES)


def cmd_fonts(args: argparse.Namespace) -> int:
    dest = paths.FONTS
    dest.mkdir(parents=True, exist_ok=True)
    if all((dest / f).exists() for f in GOOGLE_FONTS_NAMES):
        print(f"폰트가 이미 있습니다: {dest}")
        return 0
    if _from_google_fonts(dest) or _from_pypi(dest):
        print(f"폰트 준비 완료: {dest}")
        return 0
    print("폰트를 내려받지 못했습니다. 나눔고딕 TTF 를 assets/fonts/ 에 직접 넣어 주세요.", file=sys.stderr)
    return 1


def config_findings(cfg) -> list[tuple[str, str]]:
    """비밀값이 아닌 channel.yaml 설정 중 발행을 실패시키거나 귀속을 끊는 빈칸. ('X' 문제 | '!' 경고 | 'O' 정상)

    리허설(2026-W42)에서 `publish due` 가 티스토리 글을 blog_name 빈칸으로 실패시켰는데 doctor 는 비밀값만 봐서 몰랐다.
    """
    import re

    # 발행기와 같은 규칙으로 본다
    from studio.publish.tistory import normalize_blog_name

    out: list[tuple[str, str]] = []
    t = cfg.platforms.tistory
    if t.enabled:
        blog = normalize_blog_name(t.blog_name or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", blog):
            out.append(("X", ("platforms.tistory.blog_name 이 비었거나 형식이 틀렸습니다 — 티스토리 발행이 실패합니다"
                              "(https://<이름>.tistory.com 의 <이름>, 또는 platforms.tistory.enabled: false)")))
        else:
            out.append(("O", f"티스토리 블로그 {blog}.tistory.com"))
    if not (cfg.app.link_base or "").strip():
        out.append(("!", ("app.link_base 가 비어 있습니다 — 설명란·블로그의 앱 링크가 추적 링크가 아니라 클릭·설치 귀속이 잡히지 않습니다"
                          "(Supabase 함수 /go 주소를 넣으세요, docs/SETUP.md)")))
    return out


def cmd_doctor(args: argparse.Namespace) -> int:
    load_env()
    cfg = load_config()
    problems = 0
    print(f"채널: {cfg.channel.name} / 주제: {cfg.channel.topic}")
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool):
            print(f"[O] {tool}")
        else:
            print(f"[X] {tool} 이 없습니다 (영상 렌더링에 필요)")
            problems += 1
    for f in (cfg.brand.font_regular, cfg.brand.font_bold):
        if (paths.FONTS / f).exists():
            print(f"[O] 폰트 {f}")
        else:
            print(f"[X] 폰트 {f} 없음 → python -m studio setup fonts")
            problems += 1
    print("── channel.yaml 설정 점검")
    for level, msg in config_findings(cfg):
        print(f"[{level}] {msg}")
        problems += level == "X"
    try:
        from studio.secrets_spec import check_secrets  # 플랫폼별 필요 비밀값 점검

        problems += check_secrets(cfg)
    except ModuleNotFoundError:
        pass
    print("점검 완료: " + ("문제 없음" if problems == 0 else f"문제 {problems}건"))
    return 1 if problems else 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("setup", help="설치 보조")
    s = p.add_subparsers(dest="setup_cmd", metavar="<하위명령>")
    f = s.add_parser("fonts", help="한글 폰트 내려받기")
    f.set_defaults(func=cmd_fonts)
    d = sub.add_parser("doctor", help="설정·비밀값·도구 점검")
    d.set_defaults(func=cmd_doctor)
