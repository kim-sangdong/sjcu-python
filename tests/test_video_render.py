"""영상 엔진 통합 테스트: 픽스처 원고를 무음 TTS 로 실제 렌더해 결과 파일을 검사한다.

STUDIO_ROOT 를 tmp_path 로 바꾸고(paths 모듈 값도 같이), 폰트만 저장소 것을 쓴다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pytest

from studio import paths
from studio.config import load_config

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"
WEEK = "2026-W42"

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe 가 필요합니다")


@pytest.fixture
def studio_root(tmp_path, monkeypatch):
    for name, sub in {"ROOT": "", "CONTENT": "content", "OUT": "out", "ARCHIVE": "archive", "REPORTS": "reports",
                      "SECRETS": ".secrets", "ASSETS": "assets"}.items():
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(paths, "FONTS", REPO / "assets" / "fonts")
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.setenv("STUDIO_TTS", "silent")
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    shutil.copy(REPO / "channel.yaml", tmp_path / "channel.yaml")
    load_config.cache_clear()
    yield tmp_path
    load_config.cache_clear()


def _place(root: Path, fixture: str, sub: str) -> str:
    data = json.loads((FIX / fixture).read_text(encoding="utf-8"))
    d = root / "content" / "weeks" / WEEK / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{data['id']}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data["id"]


def _make_voice(root: Path) -> Path:
    """사람 녹음 대신 쓸 짧은 WAV(소리 크기가 출렁이는 사인파)."""
    p = root / "assets" / "voice" / "test_voice.wav"
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
                    "-af", "volume='0.5+0.45*sin(2*PI*t*1.3)':eval=frame", "-ar", "44100", str(p)], check=True)
    return p


def _run_cli(argv: list[str]) -> int:
    """다른 모듈과 무관하게 영상 CLI 만 붙인 파서로 실행."""
    from studio.video import cli as vcli

    parser = argparse.ArgumentParser(prog="studio")
    sub = parser.add_subparsers(dest="command")
    vcli.register(sub)
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, check=True).stdout
    return json.loads(out)


def _top_atoms(path: Path) -> list[str]:
    data = path.read_bytes()
    pos, atoms = 0, []
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        if size < 8:
            break
        atoms.append(typ.decode("latin-1"))
        pos += size
    return atoms


def _check_video(root: Path, asset_id: str, size: tuple[int, int], cover_role: str) -> tuple[dict, dict]:
    from studio.manifest import load_manifest

    out = root / "out" / "weeks" / WEEK / asset_id
    video = out / "video.mp4"
    for name in ("video.mp4", "subtitles.srt", "timeline.json", "thumbnail.png" if cover_role == "thumbnail" else "cover.png"):
        assert (out / name).exists(), f"{name} 가 없습니다"
    tl = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
    info = _probe(video)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    aus = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (vs["width"], vs["height"]) == size
    assert vs["codec_name"] == "h264" and vs["pix_fmt"] == "yuv420p"
    assert vs["r_frame_rate"] == "30/1"
    assert aus["codec_name"] == "aac" and int(aus["sample_rate"]) == 48000
    dur = float(info["format"]["duration"])
    assert abs(dur - tl["duration"]) <= 0.5, f"영상 {dur:.2f}초 ↔ 타임라인 {tl['duration']:.2f}초"
    # 타임라인의 장면 합 = 전체 길이
    assert abs(tl["scenes"][-1]["end"] - tl["duration"]) < 0.05
    atoms = _top_atoms(video)
    assert atoms.index("moov") < atoms.index("mdat"), f"faststart 가 아닙니다: {atoms}"
    # manifest 기록(ROOT 기준 상대경로)
    asset = load_manifest(WEEK).get(asset_id)
    assert asset is not None
    assert set(asset.files) == {"video", cover_role, "subtitles", "timeline"}
    for rel in asset.files.values():
        assert not Path(rel).is_absolute() and (root / rel).exists()
    for key in ("chapters", "scene_count", "static_ratio", "tts_provider"):
        assert key in asset.meta
    assert asset.meta["tts_provider"] == "silent"
    assert 0.0 <= asset.meta["static_ratio"] < 1.0
    return tl, asset.meta


def test_render_short(studio_root, capsys):
    aid = _place(studio_root, "video_short.json", "shorts")
    t = time.time()
    assert _run_cli(["render", "video", "--week", WEEK, "--only", aid, "--tts", "silent"]) == 0
    elapsed = time.time() - t
    tl, meta = _check_video(studio_root, aid, (1080, 1920), "cover")
    print(f"\n[렌더 시간] 쇼츠 {tl['duration']:.1f}초 영상 → {elapsed:.1f}초 (세그먼트 {tl['segments']})")
    assert meta["scene_count"] == 3
    kinds = [s["kind"] for s in tl["scenes"]]
    assert kinds == ["quiz", "compare", "cta"]
    quiz = tl["scenes"][0]
    live = [b for b in quiz["beats"] if b["live"]]
    assert live and live[0]["kind"] == "hold" and abs((live[0]["end"] - live[0]["start"]) - 5.0) < 0.05   # 카운트다운 5초
    assert len(tl["sentences"]) == 10
    srt = (studio_root / "out" / "weeks" / WEEK / aid / "subtitles.srt").read_text(encoding="utf-8")
    assert "-->" in srt and "**" not in srt
    from PIL import Image

    with Image.open(studio_root / "out" / "weeks" / WEEK / aid / "cover.png") as im:
        assert im.size == (1080, 1920)


def test_render_longform_with_voice(studio_root):
    _make_voice(studio_root)
    aid = _place(studio_root, "video_longform.json", "longform")
    t = time.time()
    assert _run_cli(["render", "video", "--week", WEEK, "--kind", "longform", "--tts", "silent"]) == 0
    elapsed = time.time() - t
    tl, meta = _check_video(studio_root, aid, (1920, 1080), "thumbnail")
    print(f"\n[렌더 시간] 롱폼 {tl['duration']:.1f}초 영상 → {elapsed:.1f}초 (세그먼트 {tl['segments']})")
    assert [c[1] for c in meta["chapters"]] == ["들어가며", "원리로 구분하기"]
    assert meta["chapters"][0][0] == 0.0 and meta["chapters"][1][0] > 0
    assert meta["human_voice_seconds"] > 5.0
    voice = next(s for s in tl["scenes"] if s["kind"] == "voice")
    assert any(b["kind"] == "voice" and b["live"] for b in voice["beats"])
    thumb = studio_root / "out" / "weeks" / WEEK / aid / "thumbnail.png"
    from PIL import Image

    with Image.open(thumb) as im:
        assert im.size == (1280, 720)
    assert thumb.stat().st_size <= 2 * 1024 * 1024


def test_render_cli_reports_missing(studio_root, capsys):
    assert _run_cli(["render", "video", "--week", WEEK]) == 2


def test_render_bad_scene_kind_fails_one_asset(studio_root):
    data = json.loads((FIX / "video_short.json").read_text(encoding="utf-8"))
    data["chapters"][0]["scenes"][0]["kind"] = "no_such_kind"
    d = studio_root / "content" / "weeks" / WEEK / "shorts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{data['id']}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert _run_cli(["render", "video", "--week", WEEK, "--tts", "silent"]) == 1
