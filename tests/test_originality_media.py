"""독창성 검사 렌더 단계(썸네일·프레임 재사용·정지 화면) 테스트. ffmpeg 로 짧은 테스트 영상을 만든다. 네트워크를 쓰지 않는다."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from studio import cliutil, config, paths
from studio.cli import main as _studio_main
from studio.config import Config
from studio.manifest import upsert_asset
from studio.models import RenderedAsset
from studio.originality import visual as V
from studio.originality.checker import run_check

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg 필요")

FIX = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]
FONT = REPO / "assets" / "fonts" / "NanumGothicBold.ttf"
WEEK, NEXT = "2026-W42", "2026-W43"
SIZE = (180, 320)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


def studio_main(argv: list[str]) -> int:
    cliutil._GROUPS.clear()
    return _studio_main(argv)


# ---------------------------------------------------------------- 테스트 미디어

def blocks(path: Path, seed: int) -> Path:
    """서로 확실히 다른 화면: 무작위 색 사각형 12개."""
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", SIZE, tuple(int(x) for x in rng.integers(0, 255, 3)))
    d = ImageDraw.Draw(im)
    for _ in range(12):
        x0, y0 = int(rng.integers(0, SIZE[0])), int(rng.integers(0, SIZE[1]))
        w, h = (int(x) for x in rng.integers(20, 90, 2))
        d.rectangle([x0, y0, x0 + w, y0 + h], fill=tuple(int(x) for x in rng.integers(0, 255, 3)))
    im.save(path)
    return path


def slideshow(images: list[Path], out: Path, *, gop: int = 10) -> Path:
    """이미지마다 1초, 10fps, 키프레임 gop 프레임마다(렌더러처럼 촘촘하게)."""
    lst = out.with_suffix(".txt")
    lines = []
    for p in images:
        lines += [f"file '{p}'", "duration 1"]
    lines.append(f"file '{images[-1]}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", "fps=10,format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0", str(out)], check=True)
    return out


def still_video(image: Path, out: Path, seconds: int = 20) -> Path:
    """한 화면에 시간 글자만 바뀌는 영상(진행 막대만 움직이는 슬라이드와 같은 경우). 기본 GOP → 키프레임이 성겨 전체 디코딩 경로."""
    vf = f"fps=10,drawtext=fontfile={FONT}:text='%{{pts\\:hms}}':x=4:y=4:fontsize=11:fontcolor=white,format=yuv420p"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(image), "-t", str(seconds), "-vf", vf,
                    "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True)
    return out


def cover(path: Path, text: str, bg: tuple[int, int, int], fg: tuple[int, int, int], *, layout: int = 0) -> Path:
    im = Image.new("RGB", (540, 960), bg)
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONT), 72)
    if layout == 0:
        d.rectangle([40, 300, 500, 660], fill=fg)
        d.text((70, 420), text, font=font, fill=bg)
    else:
        d.ellipse([60, 80, 480, 500], fill=fg)
        d.text((60, 700), text, font=font, fill=fg)
    im.save(path)
    return path


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("media")
    img = {s: blocks(d / f"b{s}.png", s) for s in list(range(10)) + [100, 101, 102, 103] + list(range(200, 210)) + [300]}
    m = {
        "vA": slideshow([img[s] for s in range(10)], d / "a.mp4"),
        "vB": slideshow([img[s] for s in [0, 1, 2, 3, 4, 5, 100, 101, 102, 103]], d / "b.mp4"),
        "vC": slideshow([img[s] for s in range(200, 210)], d / "c.mp4"),
        "vStill": still_video(img[300], d / "still.mp4"),
        "coverA": cover(d / "coverA.png", "복습의 비밀", (250, 240, 230), (140, 33, 41)),
        "coverC": cover(d / "coverC.png", "필기 vs 녹음", (30, 60, 90), (240, 160, 100), layout=1),
        "coverD": cover(d / "coverD.png", "공부 시간", (220, 250, 220), (20, 90, 40), layout=1),
    }
    near = ImageEnhance.Brightness(Image.open(m["coverA"]).convert("RGB")).enhance(1.04)
    near.save(d / "coverA2.jpg", quality=70)
    m["coverA2"] = d / "coverA2.jpg"
    inv = ImageOps.invert(Image.open(m["coverA"]).convert("RGB"))
    inv.save(d / "coverA_inv.png")
    m["coverA_inv"] = d / "coverA_inv.png"
    return m


def pool() -> list[dict]:
    return json.loads((FIX / "orig_shorts_pool.json").read_text(encoding="utf-8"))["items"]


def put_short(data: dict, n: int, week: str = WEEK) -> str:
    d = copy.deepcopy(data)
    d["id"], d["week"] = paths.short_id(week, n), week
    p = paths.content_dir(week) / "shorts" / f"{d['id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d["id"]


def rendered(week: str, aid: str, video: Path, cover_: Path, *, timeline: list[dict] | None = None, kind: str = "short") -> None:
    files = {"video": str(video), "cover": str(cover_)}
    if timeline is not None:
        tl = paths.out_dir(week) / aid / "timeline.json"
        tl.parent.mkdir(parents=True, exist_ok=True)
        tl.write_text(json.dumps({"scenes": timeline}), encoding="utf-8")
        files["timeline"] = str(tl.relative_to(paths.ROOT))
    upsert_asset(week, RenderedAsset(asset_id=aid, kind=kind, files=files, meta={"tts_provider": "silent"}))


def metric(report, aid: str, name: str):
    a = next(a for a in report.assets if a.asset_id == aid)
    return next(c for c in a.checks if c.metric == name)


def cfg(**orig) -> Config:
    return Config.model_validate({"originality": {"voice_week": "off", **orig}})


# ---------------------------------------------------------------- 단위

def test_freeze_stats_on_synthetic_frames():
    still = np.full((16, 32), 120, dtype=np.uint8)
    frames = [still.copy() for _ in range(11)]                       # 0~10초 정지(10초)
    rng = np.random.default_rng(1)
    frames += [rng.integers(0, 255, (16, 32), dtype=np.uint8) for _ in range(5)]   # 11~15초 매초 바뀜
    sample = V.FrameSample(np.arange(16, dtype=float), np.stack(frames), "full", 16.0)
    fz = V.freeze_stats(sample, 0.5, 2.0)
    assert fz.longest == 10.0 and fz.longest_span == (0.0, 10.0)
    assert fz.ratio == pytest.approx(10 / 16, abs=0.01)


def test_thumb_distance_catches_color_inversion_and_reencode(media):
    a = V.thumb_hash(media["coverA"])
    inv = V.thumb_hash(media["coverA_inv"])
    near = V.thumb_hash(media["coverA2"])
    other = V.thumb_hash(media["coverC"])
    assert V.hamming(a.phash, inv.phash) > 150                    # 순수 pHash 로는 '완전히 다름'
    assert V.thumb_distance(inv, a.phash, a.edge) <= 28           # 반전 비교로 잡힌다
    assert V.thumb_distance(near, a.phash, a.edge) <= 28
    assert V.thumb_distance(other, a.phash, a.edge) > 40


def test_decode_modes(media):
    key = V.decode(media["vA"])
    assert key.mode == "key"
    assert 9 <= len(key.frames) <= 11 and key.times[1] == pytest.approx(1.0, abs=0.05)
    full = V.decode(media["vStill"], fps=2.0)
    assert full.mode == "full" and 38 <= len(full.frames) <= 41


# ---------------------------------------------------------------- 통합(render 단계)

def test_render_stage_visual_checks(root, media):
    p = pool()
    s1 = put_short(p[0], 1)
    s2 = put_short(p[5], 2)
    s3 = put_short(p[6], 3)
    s4 = put_short(p[7], 4)
    s5 = put_short(p[8], 5)
    rendered(WEEK, s1, media["vA"], media["coverA"])
    rendered(WEEK, s2, media["vB"], media["coverA2"])
    rendered(WEEK, s3, media["vC"], media["coverC"],
             timeline=[{"id": "g1", "kind": "title", "start": 0, "end": 1}, {"id": "g2", "kind": "flow", "start": 1, "end": 7},
                       {"id": "g3", "kind": "cta", "start": 7, "end": 10}])
    rendered(WEEK, s4, media["vStill"], media["coverD"])
    report, _, elapsed = run_check(WEEK, "render", cfg=cfg())
    # 앞 6초 화면을 재사용 → 차단, 커버 거의 같음 → 차단
    fr = metric(report, s2, "frame_reuse")
    assert fr.value >= 0.5 and fr.verdict == "block" and fr.compared_with == s1
    th = metric(report, s2, "thumb_dist")
    assert th.value <= 28 and th.verdict == "block" and th.compared_with == s1
    a2 = next(a for a in report.assets if a.asset_id == s2)
    assert any("화면과 같음(00:00~" in f for f in a2.fixes)
    # 새 화면·새 커버 → 통과, cta 장면 화면은 비교에서 뺀다(10초 중 7장 이하)
    assert metric(report, s3, "frame_reuse").verdict == "pass" and metric(report, s3, "thumb_dist").verdict == "pass"
    assert "중 0장" in metric(report, s3, "frame_reuse").detail and "화면 표본 7장" in metric(report, s3, "frame_reuse").detail
    # 키프레임 표본 + 장면마다 바뀌는 화면 → 정지 통과
    assert "표본 방식 key" in metric(report, s1, "render_missing").detail
    assert metric(report, s1, "freeze_ratio").verdict == "pass" and metric(report, s1, "longest_freeze").value <= 2
    # 20초 내내 같은 화면(시간 글자만 바뀜) → 정지 비율·최장 정지 차단
    assert metric(report, s4, "freeze_ratio").verdict == "block"
    assert metric(report, s4, "longest_freeze").value >= 15 and metric(report, s4, "longest_freeze").verdict == "block"
    assert "표본 방식 full" in metric(report, s4, "render_missing").detail
    # 렌더 결과가 없으면 검사를 건너뛰지 않고 차단
    assert metric(report, s5, "render_missing").verdict == "block"
    a5 = next(a for a in report.assets if a.asset_id == s5)
    assert any(f"--only {s5}" in f for f in a5.fixes)
    assert elapsed < 60


def test_archive_frames_compared_next_week(root, media):
    p = pool()
    s1 = put_short(p[0], 1)
    s2 = put_short(p[5], 2)
    rendered(WEEK, s1, media["vA"], media["coverA"])
    rendered(WEEK, s2, media["vStill"], media["coverD"])
    assert studio_main(["check", "originality", "--week", WEEK, "--stage", "render"]) == 2     # S02 정지 화면 차단
    assert studio_main(["archive", "add", "--week", WEEK]) == 0
    idx = json.loads(paths.archive_index().read_text(encoding="utf-8"))
    assert [a["asset_id"] for a in idx["assets"]] == [s1]                                     # 차단된 S02 는 넣지 않는다
    e = idx["assets"][0]
    assert 8 <= len(e["frame_phashes"]) <= 10 and all(len(h) == 64 for h in e["frame_phashes"])
    assert len(e["thumb_phash"]) == 64 and len(e["thumb_edge_phash"]) == 64
    # 다음 주: 글은 새로 썼지만 지난주 화면·커버를 다시 씀 → 과거와 비교해 차단
    n1 = put_short(p[1], 1, week=NEXT)
    rendered(NEXT, n1, media["vB"], media["coverA2"])
    report, _, _ = run_check(NEXT, "render", cfg=cfg())
    fr = metric(report, n1, "frame_reuse")
    assert fr.verdict == "block" and fr.compared_with == s1
    assert metric(report, n1, "thumb_dist").verdict == "block"
    assert metric(report, n1, "script_sim").verdict == "pass"


def test_cardnews_near_identical_slides(root, media, tmp_path):
    slides = []
    for i in range(3):
        slides.append(blocks(tmp_path / f"s{i}.png", 500 + i))
    copies = []
    for i, s in enumerate(slides):
        q = tmp_path / f"c{i}.jpg"
        ImageEnhance.Brightness(Image.open(s).convert("RGB")).enhance(1.03).save(q, quality=80)
        copies.append(q)
    for n, files, title in ((1, slides, "복습 카드"), (2, copies, "필기 카드")):
        card = {"id": paths.cardnews_id(WEEK, n), "week": WEEK, "title": title, "caption": f"{title} 설명",
                "slides": [{"kind": "cover", "title": title}, {"kind": "body", "title": f"{title} 본문", "body": f"{title}에 대한 서로 다른 설명 {n}"},
                           {"kind": "list", "title": "정리", "body": f"항목 {n}"}]}
        p = paths.content_dir(WEEK) / "cardnews" / f"{card['id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
        upsert_asset(WEEK, RenderedAsset(asset_id=card["id"], kind="cardnews", files={f"slide{i + 1:02d}": str(f) for i, f in enumerate(files)}))
    report, _, _ = run_check(WEEK, "render", cfg=cfg())
    c2 = paths.cardnews_id(WEEK, 2)
    assert metric(report, c2, "thumb_dist").verdict == "block"
    fr = metric(report, c2, "frame_reuse")
    assert fr.value == 1.0 and fr.verdict == "block"
