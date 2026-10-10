"""시각 지표: 썸네일 pHash, 영상 프레임 표본 pHash, 프레임 차이로 본 정지 구간.

해시는 256비트 pHash(imagehash hash_size=16)를 쓴다. 이 저장소 렌더러는 모든 화면이 같은 브랜드 틀(배경·글꼴·배치)이라
64비트 pHash 로는 '같은 틀·다른 내용'(8~16)과 '같은 화면 재사용'(0~10)이 겹쳤다. 256비트에서는 62~98 대 0~32 로 갈린다 [실험].

영상 디코딩은 키프레임만(-skip_frame nokey) 읽는다. 렌더러가 2초마다 키프레임을 넣으므로(-g fps*2) 0.5fps 이상 표본이 되고
28분 롱폼도 몇 초면 끝난다(전체 디코딩은 2코어 기준 1분 이상). 키프레임 간격이 2.5초를 넘는 외부 영상은 전체 디코딩으로 바꾼다.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image, ImageFilter, ImageOps

PHASH_SIZE = 16                 # 16×16 = 256비트
HEX_LEN = PHASH_SIZE * PHASH_SIZE // 4
LOW_INFO_STD = 8.0              # 회색조 표준편차가 이보다 낮은 프레임(단색 화면)은 버린다(PDQ 품질 필터와 같은 역할)
DEDUPE_BITS = 16                # 직전 표본과 이 거리 이하이면 같은 화면으로 보고 하나만 남긴다
DECODE_LONG_SIDE = 256          # 분석용 디코딩 크기(긴 변)
KEYFRAME_MAX_GAP = 2.5          # 키프레임 간격이 이보다 길면 전체 디코딩
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


class MediaError(RuntimeError):
    pass


# ---------------------------------------------------------------- 해시 도구

def phash_int(img: Image.Image) -> int:
    return int(str(imagehash.phash(img, hash_size=PHASH_SIZE)), 16)


def to_hex(h: int) -> str:
    return f"{h:0{HEX_LEN}x}"


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def words(hexes: list[str]) -> np.ndarray:
    """256비트 hex 목록 → (n, 4) uint64."""
    out = np.zeros((len(hexes), 4), dtype=np.uint64)
    for i, h in enumerate(hexes):
        h = h.rjust(HEX_LEN, "0")
        for j in range(4):
            out[i, j] = int(h[j * 16:(j + 1) * 16], 16)
    return out


def match_matrix(q: np.ndarray, c: np.ndarray, bits: int, chunk: int = 16) -> np.ndarray:
    """(len(q), len(c)) bool — 해밍거리 ≤ bits."""
    out = np.zeros((len(q), len(c)), dtype=bool)
    if len(q) == 0 or len(c) == 0:
        return out
    for i in range(0, len(q), chunk):
        x = q[i:i + chunk, None, :] ^ c[None, :, :]
        out[i:i + chunk] = np.bitwise_count(x).sum(axis=-1, dtype=np.int32) <= bits
    return out


# ---------------------------------------------------------------- 썸네일

@dataclass
class ThumbHash:
    phash: int
    inv: int      # 색 반전 — 배경·글자색만 바꾼 경우를 잡는다
    edge: int     # 윤곽선 — 색 구성이 달라도 배치가 같으면 잡는다

    @property
    def hex(self) -> str:
        return to_hex(self.phash)

    @property
    def edge_hex(self) -> str:
        return to_hex(self.edge)


def thumb_hash(path: Path) -> ThumbHash:
    with Image.open(path) as src:
        im = src.convert("RGB")
    g = ImageOps.grayscale(im)
    h = max(8, round(256 * im.height / max(1, im.width)))
    edge = g.resize((256, h)).filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
    return ThumbHash(phash_int(im), phash_int(ImageOps.invert(im)), phash_int(edge))


def thumb_distance(a: ThumbHash, b_phash: int, b_edge: int | None = None) -> int:
    d = min(hamming(a.phash, b_phash), hamming(a.inv, b_phash))
    if b_edge is not None:
        d = min(d, hamming(a.edge, b_edge))
    return d


# ---------------------------------------------------------------- 영상 디코딩

@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float


def probe(path: Path) -> VideoInfo:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise MediaError(f"영상 정보를 읽지 못했습니다({path.name}): {r.stderr.strip()[-300:]}")
    data = json.loads(r.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    try:
        return VideoInfo(int(st["width"]), int(st["height"]), float(data["format"]["duration"]))
    except (KeyError, ValueError, TypeError) as e:
        raise MediaError(f"영상 스트림이 없습니다({path.name}). 렌더를 다시 하세요.") from e


def _size(info: VideoInfo) -> tuple[int, int]:
    s = DECODE_LONG_SIDE / max(info.width, info.height)
    w = max(16, int(round(info.width * s / 2)) * 2)
    h = max(16, int(round(info.height * s / 2)) * 2)
    return w, h


@dataclass
class FrameSample:
    times: np.ndarray       # (N,) 초
    frames: np.ndarray      # (N, h, w) uint8 회색조
    mode: str               # key | full
    duration: float


_PTS = re.compile(r"pts_time:\s*(-?[\d.]+)")


def _ffmpeg_raw(args: list[str], w: int, h: int, what: str) -> tuple[np.ndarray, str]:
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", *args], capture_output=True)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0:
        raise MediaError(f"ffmpeg {what} 실패: {err.strip()[-300:]}")
    n = len(r.stdout) // (w * h)
    return np.frombuffer(r.stdout[: n * w * h], dtype=np.uint8).reshape(n, h, w), err


def decode(path: Path, info: VideoInfo | None = None, *, fps: float = 1.0) -> FrameSample:
    """키프레임 표본을 먼저 시도하고, 간격이 성기면 fps 로 전체 디코딩한다."""
    info = info or probe(path)
    w, h = _size(info)
    vf = f"scale={w}:{h}:flags=area,format=gray"
    frames, err = _ffmpeg_raw(["-threads", "2", "-skip_frame", "nokey", "-i", str(path), "-map", "0:v:0",
                               "-vf", vf + ",showinfo", "-fps_mode", "passthrough", "-f", "rawvideo", "-"], w, h, "키프레임 추출")
    ts = np.array([float(x) for x in _PTS.findall(err)], dtype=np.float64)
    if len(frames) >= 2 and len(ts) == len(frames):
        gaps = np.diff(np.append(ts, info.duration))
        if gaps.max(initial=0) <= KEYFRAME_MAX_GAP:
            return FrameSample(ts, frames, "key", info.duration)
    frames, _ = _ffmpeg_raw(["-threads", "2", "-i", str(path), "-map", "0:v:0", "-vf", f"fps={fps}," + vf,
                             "-f", "rawvideo", "-"], w, h, "프레임 추출")
    return FrameSample(np.arange(len(frames), dtype=np.float64) / fps, frames, "full", info.duration)


# ---------------------------------------------------------------- 정지 구간

@dataclass
class FreezeStats:
    ratio: float                       # 정지(min_freeze 이상) 구간 합 ÷ 길이
    longest: float                     # 최장 정지(초)
    longest_span: tuple[float, float]  # (시작, 끝)
    runs: list[tuple[float, float]] = field(default_factory=list)


def freeze_stats(sample: FrameSample, still_diff: float, min_freeze: float) -> FreezeStats:
    """이웃 표본의 회색조 평균 절대차가 still_diff 미만이면 그 사이를 정지로 본다."""
    f, t = sample.frames, sample.times
    runs: list[tuple[float, float]] = []
    start: float | None = None
    for i in range(len(f) - 1):
        d = float(np.abs(f[i + 1].astype(np.int16) - f[i].astype(np.int16)).mean())
        if d < still_diff:
            start = t[i] if start is None else start
            end = t[i + 1]
        elif start is not None:
            runs.append((float(start), float(end)))
            start = None
    if start is not None:
        runs.append((float(start), float(end)))
    long_runs = [r for r in runs if r[1] - r[0] >= min_freeze - 1e-6]
    total = sum(r[1] - r[0] for r in long_runs)
    longest = max(runs, key=lambda r: r[1] - r[0], default=(0.0, 0.0))
    return FreezeStats(round(total / max(sample.duration, 1e-6), 4), round(longest[1] - longest[0], 2), longest, long_runs)


# ---------------------------------------------------------------- 프레임 표본 해시

@dataclass
class FrameHashes:
    hexes: list[str]
    times: list[float]

    @property
    def words(self) -> np.ndarray:
        return words(self.hexes)


def frame_hashes(sample: FrameSample, *, max_frames: int, min_interval: float = 1.0,
                 exclude: list[tuple[float, float]] | None = None) -> FrameHashes:
    """1fps 이하로 고르고(최대 max_frames 장, 고르게), 단색 화면·제외 구간을 뺀 뒤, 연속 같은 화면을 하나로."""
    idx: list[int] = []
    last = -1e9
    for i, ts in enumerate(sample.times):
        if ts - last >= min_interval - 1e-6:
            idx.append(i)
            last = ts
    if len(idx) > max_frames:
        pick = np.linspace(0, len(idx) - 1, max_frames).round().astype(int)
        idx = [idx[k] for k in pick]
    exclude = exclude or []
    hexes: list[str] = []
    times: list[float] = []
    prev: int | None = None
    for i in idx:
        ts = float(sample.times[i])
        if any(a <= ts < b for a, b in exclude):
            continue
        fr = sample.frames[i]
        if float(fr.std()) < LOW_INFO_STD:
            continue
        h = phash_int(Image.fromarray(fr))
        if prev is not None and hamming(h, prev) <= DEDUPE_BITS:
            continue
        prev = h
        hexes.append(to_hex(h))
        times.append(round(ts, 2))
    return FrameHashes(hexes, times)


def image_hashes(paths_: list[Path]) -> FrameHashes:
    """카드뉴스 슬라이드처럼 이미지 묶음을 프레임 표본처럼 다룬다."""
    hexes, times = [], []
    for i, p in enumerate(paths_):
        with Image.open(p) as src:
            g = ImageOps.grayscale(src.convert("RGB"))
        if float(np.asarray(g).std()) < LOW_INFO_STD:
            continue
        hexes.append(to_hex(phash_int(g)))
        times.append(float(i + 1))
    return FrameHashes(hexes, times)


# ---------------------------------------------------------------- 렌더 타임라인

def timeline_scenes(path: Path | None) -> list[dict]:
    """video 모듈의 timeline.json → [{id, kind, start, end}] (없으면 빈 목록)."""
    if not path or not Path(path).exists():
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [{"id": s["id"], "kind": s["kind"], "start": float(s["start"]), "end": float(s["end"])} for s in data.get("scenes", [])]
    except (ValueError, KeyError, TypeError):
        return []


def mmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60:02d}:{sec % 60:02d}"
