"""시험 장면 통합 렌더: 픽스처 원고(새 kind 전부 + flow hidden)를 무음 TTS 로 세로(쇼츠)·가로(롱폼) mp4 로 렌더해 검사한다.

STUDIO_SAMPLES_DIR 를 주면 세로 영상에서 kind 별로 한 장씩(그 장면의 마지막 단계 프레임) PNG 로 저장한다.
  STUDIO_SAMPLES_DIR=out/samples/exam .venv/bin/python -m pytest tests/test_video_exam_render.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio import paths
from studio.config import load_config
from studio.models import VideoScript

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"
KINDS = ["ox", "table_blank", "relations", "calc", "diff", "ranking", "flow"]

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe 가 필요합니다")


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


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, check=True).stdout
    return json.loads(out)


def _frame(video: Path, t: float, out: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out)], check=True)
    return Image.open(out).convert("RGB")


def _render(root: Path, fixture: str):
    from studio.video.compose import RenderOptions, render_video

    script = VideoScript.model_validate(json.loads((FIX / fixture).read_text(encoding="utf-8")))
    res = render_video(script, load_config(), RenderOptions(tts="silent", log=lambda _: None), root / "out" / script.id)
    return script, res


def _check(res, size: tuple[int, int]) -> dict:
    video = res.out_dir / "video.mp4"
    info = _probe(video)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    aus = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (vs["width"], vs["height"]) == size
    assert vs["codec_name"] == "h264" and vs["pix_fmt"] == "yuv420p" and vs["r_frame_rate"] == "30/1"
    assert aus["codec_name"] == "aac" and int(aus["sample_rate"]) == 48000
    tl = res.timeline
    assert abs(float(info["format"]["duration"]) - tl["duration"]) <= 0.5
    assert abs(tl["scenes"][-1]["end"] - tl["duration"]) < 0.05
    assert sorted({s["kind"] for s in tl["scenes"]}) == sorted(KINDS)
    assert tl["segments"]["anim_frames"] > 0
    bad = [w for w in res.warnings if "넘칩니다" in w or "글자가 넘쳐" in w or "말줄임" in w]
    assert not bad, bad
    return tl


def _scene(tl: dict, kind: str) -> dict:
    return next(s for s in tl["scenes"] if s["kind"] == kind)


def test_render_exam_short_portrait(studio_root):
    script, res = _render(studio_root, "video_exam_short.json")
    tl = _check(res, (1080, 1920))
    # 장면별 길이·단계(문장 수에 따른 배분)가 timeline 에 그대로 남는다
    ox = _scene(tl, "ox")
    live = [b for b in ox["beats"] if b["live"]]
    assert len(live) == 1 and abs((live[0]["end"] - live[0]["start"]) - 3.0) < 0.05          # 3초 타이머
    fix_beat = next(b for b in ox["beats"] if b["kind"] == "speech" and b["line"] == 3)
    assert fix_beat["end"] - fix_beat["start"] >= 1.5                                       # 교정 애니메이션(1.5초)이 잘리지 않는다
    tb = _scene(tl, "table_blank")
    assert [b["step"] for b in tb["beats"] if b["live"]] == [1, 2]                           # 빈칸마다 생각할 시간
    flow = _scene(tl, "flow")
    assert [b for b in flow["beats"] if b["live"]][0]["end"] - [b for b in flow["beats"] if b["live"]][0]["start"] == pytest.approx(2.0, abs=0.05)
    # 실제 영상 프레임: 타이머가 움직이고, 교정 전후 화면이 다르다
    video = res.out_dir / "video.mp4"
    w = studio_root / "frames"
    w.mkdir()
    a = np.asarray(_frame(video, live[0]["start"] + 0.3, w / "a.png"), dtype=np.int16)
    b = np.asarray(_frame(video, live[0]["end"] - 0.3, w / "b.png"), dtype=np.int16)
    assert np.abs(a - b).mean() > 0.05
    rev = next(b_ for b_ in ox["beats"] if b_["line"] == 2)
    c = np.asarray(_frame(video, rev["end"] - 0.1, w / "c.png"), dtype=np.int16)
    d = np.asarray(_frame(video, fix_beat["start"] + 1.6, w / "d.png"), dtype=np.int16)
    assert np.abs(c - d).mean() > 0.2
    assert res.asset.meta["scene_count"] == 7 and abs(res.asset.duration_seconds - tl["duration"]) < 0.5
    # 샘플 프레임 저장(선택): kind 별 마지막 단계
    dest = os.environ.get("STUDIO_SAMPLES_DIR")
    if dest:
        d_ = Path(dest) if Path(dest).is_absolute() else REPO / dest
        d_.mkdir(parents=True, exist_ok=True)
        for s in tl["scenes"]:
            t = s["end"] - 0.25
            name = f"{s['kind']}_hidden" if s["kind"] == "flow" else s["kind"]
            _frame(video, t, d_ / f"{name}_v{s['variant']}_portrait.png")


def test_render_exam_longform_landscape(studio_root):
    script, res = _render(studio_root, "video_exam_long.json")
    tl = _check(res, (1920, 1080))
    assert [c[1] for c in res.asset.meta["chapters"]] == ["함정 지문과 숫자", "계산·개정·오답 랭킹"]
    variants = {s["kind"]: s["variant"] for s in tl["scenes"]}
    for sc in script.scenes():
        assert variants[sc.kind] == sc.params["variant"]                                     # params.variant 강제
    for s in tl["scenes"]:
        assert s["end"] - s["start"] > 3.0
