"""영상 합성: 원고(VideoScript) → TTS → 타임라인 → 세그먼트 인코딩 → 오디오 → mp4.

순서
 1. 장면 렌더러를 만든다(알 수 없는 kind·빠진 params 는 여기서 오류).
 2. 문장마다 TTS 클립(캐시) — voice 장면은 사람 녹음 파일.
 3. 타임라인: 장면마다 lead(전환) → 문장(클립 + 0.25초 쉼) → hold. 모두 프레임 단위로 맞춘다(오디오 1프레임 = 1600샘플).
 4. 세그먼트: 단계가 바뀌는 짧은 전환·카운트다운·파형 구간만 프레임을 그려 파이프로 인코딩하고,
    나머지 정지 구간은 이미지 1장을 길이만큼 반복(loop 필터)한다. 화면 위 진행 막대는 ffmpeg overlay 로 움직인다.
    모든 세그먼트는 같은 x264 인자로 인코딩한 뒤 concat demuxer(-c copy)로 잇는다.
 5. 오디오: 클립을 제자리에 놓은 내레이션 WAV + (assets/bgm/*.mp3 가 있으면) BGM → loudnorm 2-pass → AAC 48kHz 128k.
 6. 합치기(-movflags +faststart), 썸네일/커버, subtitles.srt, timeline.json, manifest 기록.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from studio import paths
from studio.config import Config, MissingSecret
from studio.models import RenderedAsset, VideoScript
from studio.video import design as D
from studio.video import media
from studio.video import tts as T
from studio.video.design import Fonts, Geometry, Palette
from studio.video.scenes import FIRST_LEAD_SECONDS, LEAD_SECONDS, Beat, LayoutError, SceneContext, SceneRenderer, get_renderer
from studio.video.subtitles import CaptionPainter, Cue, sentence_cues, write_srt

log = logging.getLogger("studio.video.compose")

GAP = 0.25                 # 문장 사이 쉼(초)
MIN_STILL_FRAMES = 8       # 이보다 짧은 정지 구간은 프레임으로 그린다(ffmpeg 실행 횟수 줄이기)
LOUDNORM_TP = -1.5
LOUDNORM_LRA = 11
BGM_FADE = 2.0
VOICE_LOUDNORM_I = -16.0   # 사람 녹음 1차 정규화


@dataclass
class RenderOptions:
    tts: str | None = None          # google | edge | silent (None → STUDIO_TTS → cfg.tts.provider)
    dry_run: bool = False
    jobs: int | None = None         # 동시에 돌릴 정지 세그먼트 인코딩 수
    threads: int = 2                # 세그먼트당 x264 스레드
    keep_work: bool = False
    preview: bool = False           # 장면별 미리보기 PNG(out/.../preview/)
    allow_overflow: bool = False    # True 면 화면 넘침·말줄임(LayoutError)을 경고로만 남기고 계속 렌더(사람이 미리보기로 확인할 때만)
    log: Callable[[str], None] = print


@dataclass
class RenderResult:
    asset: RenderedAsset
    out_dir: Path
    timeline: dict
    seconds: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class TBeat:
    scene: int
    beat: Beat
    start: int = 0           # 시작 프레임
    frames: int = 0
    clip: Path | None = None
    clip_secs: float = 0.0
    audio: np.ndarray | None = None   # 효과음·사람 녹음(이 비트 시작에 놓음)


# ---------------------------------------------------------------- 도우미

def rel(p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(paths.ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def asset_out_dir(week: str, asset_id: str) -> Path:
    return paths.out_dir(week) / asset_id


def _voice_wav(src: Path) -> Path:
    h = hashlib.sha256(src.read_bytes()).hexdigest()[:24]
    dst = paths.OUT / "cache" / "voice" / f"{h}.wav"
    if not dst.exists():
        media.to_wav(src, dst, loudnorm_i=VOICE_LOUDNORM_I, highpass=True)
    return dst


def pick_bgm(cfg: Config, asset_id: str) -> Path | None:
    extra = cfg.video.model_extra or {}
    choice = extra.get("bgm")
    if choice in (False, "none", "off"):
        return None
    d = paths.ROOT / cfg.video.bgm_dir
    if isinstance(choice, str) and choice:
        p = d / choice
        return p if p.exists() else None
    files = sorted(d.glob("*.mp3")) if d.is_dir() else []
    return files[D.pick(len(files), asset_id, "bgm")] if files else None


# ---------------------------------------------------------------- 진행 막대

class Progress:
    """화면 위 진행 막대. 쇼츠는 장면별, 롱폼은 챕터별 칸. 칸 폭은 길이에 비례(채워지는 속도가 일정)."""

    def __init__(self, geo: Geometry, pal: Palette, units: list[tuple[int, int]], total: int, enabled: bool = True):
        self.g, self.pal, self.enabled = geo, pal, enabled and bool(units)
        box = geo.progress
        gap = max(6, geo.unit // 3) if len(units) > 1 else 0
        avail = box.w - gap * (len(units) - 1)
        self.units = []    # (start_f, frames, x, w)
        x = float(box.x)
        for i, (s, n) in enumerate(units):
            w = max(12.0, avail * n / max(1, total))
            if i == len(units) - 1:
                w = box.x + box.w - x
            self.units.append((s, n, int(round(x)), max(1, int(round(x + w)) - int(round(x)))))
            x += w + gap
        self.starts = [u[0] for u in self.units]
        self.y, self.h = box.y, box.h
        self.track = pal.line
        self.fill = pal.primary
        self._layers: dict[int, Image.Image] = {}

    def unit_at(self, f: int) -> int:
        return max(0, bisect.bisect_right(self.starts, f) - 1)

    def layer(self, k: int) -> Image.Image:
        """k번째 칸이 진행 중일 때의 고정 부분(트랙 + 앞 칸 채움)."""
        im = self._layers.get(k)
        if im is None:
            box = self.g.progress
            im = Image.new("RGBA", (box.w, box.h), (0, 0, 0, 0))
            for i, (_, _, x, w) in enumerate(self.units):
                im.paste(self.fill + (255,) if i < k else self.track + (255,), (x - box.x, 0, x - box.x + w, box.h))
            self._layers[k] = im
        return im

    def paint(self, canvas: Image.Image, f: int, partial: bool) -> None:
        if not self.enabled:
            return
        k = self.unit_at(f)
        canvas.alpha_composite(self.layer(k), (self.g.progress.x, self.y))
        if partial:
            s, n, x, w = self.units[k]
            fw = int(round(w * min(1.0, (f - s) / max(1, n))))
            if fw > 0:
                canvas.paste(self.fill + (255,), (x, self.y, x + fw, self.y + self.h))

    def motion(self, f0: int) -> dict | None:
        """정지 세그먼트에서 ffmpeg 가 움직일 막대 정보."""
        if not self.enabled:
            return None
        k = self.unit_at(f0)
        s, n, x, w = self.units[k]
        return {"x": x, "w": w, "y": self.y, "h": self.h, "off": f0 - s, "unit": max(1, n),
                "color": "0x%02X%02X%02X" % self.fill}


# ---------------------------------------------------------------- 인코더

class Encoder:
    """세그먼트 인코딩. 정지 구간은 스레드 풀에서 ffmpeg 로, 움직이는 구간은 호출한 스레드에서 프레임을 파이프로."""

    def __init__(self, work: Path, geo: Geometry, jobs: int, threads: int):
        self.work, self.g, self.threads = work, geo, threads
        self.pool = ThreadPoolExecutor(max_workers=max(1, jobs))
        self.slots = threading.BoundedSemaphore(max(1, jobs) * 2)
        self.segments: list[Path] = []
        self.futures: list[Future] = []
        self.still_frames = 0
        self.anim_frames = 0

    def _next(self) -> Path:
        p = self.work / f"seg{len(self.segments):05d}.mp4"
        self.segments.append(p)
        return p

    def still(self, img: Image.Image, frames: int, motion: dict | None) -> None:
        out = self._next()
        png = out.with_suffix(".png")
        img.save(png, compress_level=1)
        self.still_frames += frames
        self.slots.acquire()
        fut = self.pool.submit(self._encode_still, png, out, frames, motion)
        fut.add_done_callback(lambda _f: self.slots.release())
        self.futures.append(fut)

    def _encode_still(self, png: Path, out: Path, frames: int, m: dict | None) -> None:
        fps = self.g.fps
        base = f"[0:v]format=yuv420p,loop=loop={frames - 1}:size=1:start=0,settb=1/{fps},setpts=N"
        args = ["-framerate", str(fps), "-i", str(png)]
        if m is None:
            graph = base + "[v]"
        else:
            args += ["-f", "lavfi", "-i", f"color=c={m['color']}:s={m['w']}x{m['h']}:r={fps}"]
            cx = max(0, m["x"] - m["w"])
            cw = m["x"] - cx
            mv = f"x='{m['x']}-{m['w']}+{m['w']}*({m['off']}+t*{fps})/{m['unit']}':y={m['y']}:eval=frame:shortest=1"
            if cw > 0:
                graph = (f"{base},split[a][b];[b]crop={cw}:{m['h']}:{cx}:{m['y']}[c];[1:v]settb=1/{fps},setpts=N[bar];"
                         f"[a][bar]overlay={mv}[d];[d][c]overlay=x={cx}:y={m['y']}[v]")
            else:
                graph = f"{base}[a];[1:v]settb=1/{fps},setpts=N[bar];[a][bar]overlay={mv}[v]"
        media.ffmpeg(*args, "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames),
                     *media.x264_args(fps, self.threads), str(out), what=f"정지 세그먼트 {out.name}")
        png.unlink(missing_ok=True)

    def anim(self, frames: int, make: Callable[[int], Image.Image]) -> None:
        """make(i) → i번째 프레임(RGB). 호출한 스레드에서 그려 ffmpeg 에 바로 넘긴다."""
        out = self._next()
        g = self.g
        self.anim_frames += frames
        errf = tempfile.TemporaryFile()
        cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{g.W}x{g.H}", "-framerate", str(g.fps), "-i", "-",
               "-frames:v", str(frames), *media.x264_args(g.fps, self.threads), str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf)
        try:
            for i in range(frames):
                im = make(i)
                proc.stdin.write(im.tobytes())
            proc.stdin.close()
        except BrokenPipeError:
            pass
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        rc = proc.wait()
        if rc != 0:
            errf.seek(0)
            raise media.MediaError(f"움직이는 세그먼트 {out.name} 인코딩 실패: {errf.read().decode('utf-8', 'replace')[-800:]}")
        errf.close()

    def finish(self) -> list[Path]:
        try:
            for f in self.futures:
                f.result()
        finally:
            self.pool.shutdown(wait=True)
        return self.segments


# ---------------------------------------------------------------- 오디오

def build_narration(tbeats: list[TBeat], total_frames: int, fps: int, out: Path) -> Path:
    """클립·효과음을 프레임 위치에 맞춰 놓은 모노 WAV(스트리밍으로 써서 메모리를 적게 쓴다)."""
    spf = media.SAMPLE_RATE / fps
    total = int(round(total_frames * spf))
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(media.SAMPLE_RATE)
        pos = 0
        for tb in tbeats:
            arr = media.read_wav(tb.clip) if tb.clip else tb.audio
            if arr is None or arr.size == 0:
                continue
            start = int(round(tb.start * spf))
            if start > pos:
                w.writeframes(np.zeros(start - pos, dtype=np.int16).tobytes())
                pos = start
            elif start < pos:
                arr = arr[pos - start:]
            arr = arr[: max(0, total - pos)]
            w.writeframes(np.asarray(arr, dtype=np.int16).tobytes())
            pos += arr.size
        if pos < total:
            w.writeframes(np.zeros(total - pos, dtype=np.int16).tobytes())
    return out


def _loudnorm_measure(inputs: list[str], graph: str) -> dict | None:
    res = subprocess.run([media.FFMPEG, "-hide_banner", "-nostdin", "-y", *inputs, "-filter_complex",
                          graph + ",loudnorm=print_format=json[o]", "-map", "[o]", "-f", "null", "-"],
                         capture_output=True)
    txt = res.stderr.decode("utf-8", "replace")
    s, e = txt.rfind("{"), txt.rfind("}")
    if res.returncode != 0 or s < 0:
        raise media.MediaError("loudnorm 측정 실패:\n" + "\n".join(txt.strip().splitlines()[-8:]))
    data = json.loads(txt[s:e + 1])
    try:
        if float(data["input_i"]) < -70:
            return None
    except (ValueError, KeyError):
        return None
    return data


def master_audio(narration: Path, out: Path, seconds: float, cfg: Config, asset_id: str, *, normalize: bool) -> dict:
    """내레이션(+BGM) → loudnorm(2-pass, linear) → AAC 48kHz 128k 스테레오. 반환: 처리 정보."""
    bgm = pick_bgm(cfg, asset_id)
    inputs = ["-i", str(narration)]
    graph = "[0:a]pan=stereo|c0=c0|c1=c0,aresample=48000"
    if bgm:
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        fade_out = max(0.0, seconds - BGM_FADE - 0.5)
        graph = (f"[0:a]pan=stereo|c0=c0|c1=c0,aresample=48000[n];"
                 f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={cfg.video.bgm_volume_db}dB,"
                 f"afade=t=in:d={BGM_FADE},afade=t=out:st={fade_out:.3f}:d={BGM_FADE},atrim=0:{seconds:.3f}[b];"
                 f"[n][b]amix=inputs=2:duration=first:dropout_transition=0:normalize=0")
    info = {"bgm": rel(bgm) if bgm else None, "loudnorm": None}
    target = cfg.video.loudness_lufs
    m = _loudnorm_measure(inputs, graph) if normalize else None
    if m:
        graph += (f",loudnorm=I={target}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:measured_I={m['input_i']}:"
                  f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
                  f"offset={m['target_offset']}:linear=true,aresample=48000")
        info["loudnorm"] = {"target_i": target, "measured_i": float(m["input_i"])}
    media.ffmpeg(*inputs, "-filter_complex", graph + "[o]", "-map", "[o]", "-t", f"{seconds:.3f}",
                 "-c:a", "aac", "-b:a", media.AUDIO_BITRATE, "-ar", str(media.SAMPLE_RATE), str(out), what="오디오 마스터링")
    return info


# ---------------------------------------------------------------- 본체

class VideoBuilder:
    def __init__(self, script: VideoScript, cfg: Config, opts: RenderOptions, out_dir: Path):
        self.script, self.cfg, self.opts, self.out_dir = script, cfg, opts, out_dir
        size = cfg.video.short_size if script.format == "short" else cfg.video.long_size
        self.g = Geometry.make(tuple(size), cfg.video.fps)
        self.pal = Palette.from_brand(cfg.brand)
        self.fonts = Fonts(cfg.brand)
        self.warnings: list[str] = []
        self.renderers: list[SceneRenderer] = []
        self.chapter_of: list[int] = []
        self.tts: T.CachedTTS | None = None

    # 1. 장면
    def build_scenes(self) -> None:
        motif = D.pick(3, self.script.id, "motif")
        prev = None
        i = 0
        for ci, ch in enumerate(self.script.chapters):
            for sc in ch.scenes:
                cls = get_renderer(sc.kind)
                ctx = SceneContext(cfg=self.cfg, geo=self.g, pal=self.pal, fonts=self.fonts, asset_id=self.script.id,
                                   fmt=self.script.format, index=i, chapter_title=ch.title, chapter_index=ci, motif=motif,
                                   root=paths.ROOT, prev=prev, channel_name=self.cfg.channel.name)
                r = cls(sc, ctx)
                self.renderers.append(r)
                self.chapter_of.append(ci)
                self.warnings += r.warnings
                prev = (r.kind, r.variant)
                i += 1
        defects = [d for r in self.renderers for d in r.defects]
        if defects and not self.opts.allow_overflow:
            for r in self.renderers:
                r.release()
            raise LayoutError("화면에 글이 다 들어가지 않습니다(잘린 채 발행하지 않도록 렌더를 멈춤) — 원고를 짧게 고친 뒤 다시 렌더하세요:\n  - "
                              + "\n  - ".join(defects))

    # 2. 소리
    def synthesize(self) -> dict[tuple[int, int], tuple[Path, float]]:
        jobs: list[tuple[int, int, str]] = []
        for si, r in enumerate(self.renderers):
            if r.kind == "voice":
                continue
            for li, line in enumerate(r.scene.narration):
                if T.clean_text(line):
                    jobs.append((si, li, line))
        self.tts = T.resolve(self.cfg, self.opts.tts, dry_run=self.opts.dry_run)
        texts = [j[2] for j in jobs]
        try:
            clips = self.tts.clips(texts)
        except (T.TTSError, MissingSecret) as e:
            fb = T.fallback(self.cfg, self.tts)
            if fb is None:
                raise
            self.warnings.append(f"TTS {self.tts.name} 실패 → 대체 공급자 {fb.name} 로 전체 다시 합성: {e}")
            self.opts.log(f"[경고] {self.warnings[-1]}")
            self.tts = fb
            clips = self.tts.clips(texts)
        out = {(si, li): c for (si, li, _), c in zip(jobs, clips)}
        for r in self.renderers:
            if r.kind == "voice":
                wav = _voice_wav(r.audio_path)
                samples = media.read_wav(wav)
                r.prepare_voice(samples.size / media.SAMPLE_RATE, samples, media.SAMPLE_RATE)
                r.voice_wav = wav
        return out

    # 3. 타임라인
    def timeline(self, clips) -> list[TBeat]:
        fps = self.g.fps
        tbeats: list[TBeat] = []
        f = 0
        for si, r in enumerate(self.renderers):
            first_voice = True
            for b in r.plan():
                tb = TBeat(si, b, start=f)
                if b.kind == "lead":
                    secs = FIRST_LEAD_SECONDS if si == 0 else LEAD_SECONDS
                elif b.kind == "speech":
                    clip = clips.get((si, b.line))
                    if clip:
                        tb.clip, tb.clip_secs = clip
                    secs = tb.clip_secs + GAP
                else:
                    secs = b.seconds
                    if b.kind == "voice" and first_voice:
                        tb.audio = media.read_wav(r.voice_wav)
                        first_voice = False
                    elif b.kind == "hold":
                        tb.audio = r.hold_audio(b, secs)
                tb.frames = max(1, math.ceil(secs * fps - 1e-6))
                f += tb.frames
                tbeats.append(tb)
        return tbeats

    def cues(self, tbeats: list[TBeat], painter: CaptionPainter) -> list[Cue]:
        fps = self.g.fps
        cues: list[Cue] = []
        for tb in tbeats:
            r = self.renderers[tb.scene]
            b = tb.beat
            if b.line is None or b.kind not in ("speech", "voice"):
                continue
            text = r.scene.narration[b.line]
            s = tb.start / fps
            end = (tb.start + tb.frames) / fps
            speech_end = s + tb.clip_secs if b.kind == "speech" else end
            cues += sentence_cues(text, s, speech_end, end, self.fonts, painter.size, painter.max_w, r.scene.id, b.line)
        return cues

    # 4. 세그먼트
    def encode(self, enc: Encoder, tbeats: list[TBeat], cues: list[Cue], burn: set[int], progress: Progress,
               painter: CaptionPainter) -> None:
        fps = self.g.fps
        cue_f = [(int(round(c.start * fps)), int(round(c.end * fps)), c) for c in cues]
        cue_starts = [c[0] for c in cue_f]

        def cue_at(f: int) -> Cue | None:
            i = bisect.bisect_right(cue_starts, f) - 1
            if i >= 0 and cue_f[i][0] <= f < cue_f[i][1]:
                return cue_f[i][2]
            return None

        last_step: dict[int, int] = {}
        for tb in tbeats:
            last_step[tb.scene] = tb.beat.step
        # (시작, 길이, 정지 여부, 그리기 함수(f) → RGBA, 장면)
        pieces: list[tuple[int, int, bool, Callable[[int], Image.Image], int]] = []
        cur_step = -1
        for tb in tbeats:
            r = self.renderers[tb.scene]
            b = tb.beat
            f0, n = tb.start, tb.frames
            if b.kind == "lead":
                cur_step = -1
                if r.transition == "xfade" and tb.scene > 0:
                    pr, ps = self.renderers[tb.scene - 1], last_step[tb.scene - 1]
                    pieces.append((f0, n, False, lambda f, r=r, pr=pr, ps=ps, f0=f0, n=n:
                                   Image.blend(pr.still(ps), r.still(-1), D.ease_in_out((f - f0 + 1) / n)), tb.scene))
                else:
                    pieces.append((f0, n, True, lambda f, r=r: r.still(-1), tb.scene))
                continue
            a, s = cur_step, b.step
            tsec = r.transition_seconds(a, s) if a != s else 0.0
            tf = min(n, math.ceil(tsec * fps)) if tsec > 0 else 0
            if b.live:
                def live(f, r=r, a=a, s=s, b=b, f0=f0, n=n, tsec=tsec):
                    t = (f - f0) / fps
                    im = (r.frame(a, s, t) if t < tsec else r.still(s)).copy()
                    r.live(im, b, t, n / fps)
                    return im
                pieces.append((f0, n, False, live, tb.scene))
            else:
                if tf:
                    pieces.append((f0, tf, False, lambda f, r=r, a=a, s=s, f0=f0: r.frame(a, s, (f - f0) / fps), tb.scene))
                if n - tf > 0:
                    pieces.append((f0 + tf, n - tf, True, lambda f, r=r, s=s: r.still(s), tb.scene))
            cur_step = s

        # 자막 경계에서 정지 구간 나누기
        bounds = sorted({c[0] for c in cue_f} | {c[1] for c in cue_f} | set(progress.starts if progress.enabled else []))
        split: list[tuple[int, int, bool, Callable, int]] = []
        for p0, n, still, fn, sc in pieces:
            if not still:
                split.append((p0, n, still, fn, sc))
                continue
            cuts = [x for x in bounds[bisect.bisect_right(bounds, p0):bisect.bisect_left(bounds, p0 + n)]]
            edges = [p0] + cuts + [p0 + n]
            for x0, x1 in zip(edges, edges[1:]):
                if x1 > x0:
                    split.append((x0, x1 - x0, True, fn, sc))

        def compose(content: Image.Image, f: int, partial: bool, sc: int) -> Image.Image:
            im = content.copy()
            progress.paint(im, f, partial)
            if sc in burn:
                c = cue_at(f)
                if c is not None:
                    painter.paint(im, c.text)
            return im.convert("RGB")

        # 짧은 정지 구간은 움직이는 구간으로 합치고, 이어진 움직이는 구간은 한 세그먼트로
        groups: list[list[tuple[int, int, bool, Callable, int]]] = []
        for pc in split:
            p0, n, still, fn, sc = pc
            as_anim = (not still) or n < MIN_STILL_FRAMES
            if as_anim and groups and groups[-1][0][2] is False:
                groups[-1].append((p0, n, False, fn, sc))
            elif as_anim:
                groups.append([(p0, n, False, fn, sc)])
            else:
                groups.append([pc])
        released = 0
        for grp in groups:
            # 지나간 장면의 화면 캐시는 비운다(바로 앞 장면은 크로스페이드에 필요)
            while released < grp[0][4] - 1:
                self.renderers[released].release()
                released += 1
            if grp[0][2]:
                p0, n, _, fn, sc = grp[0]
                content = fn(p0)
                enc.still(compose(content, p0, False, sc), n, progress.motion(p0))
                continue
            start = grp[0][0]
            total_n = sum(x[1] for x in grp)
            index = [(x[0], x[0] + x[1], x[3], x[4]) for x in grp]

            def make(i, start=start, index=index):
                f = start + i
                for a0, a1, fn, sc in index:
                    if a0 <= f < a1:
                        return compose(fn(f), f, True, sc)
                raise RuntimeError("프레임 범위 오류")
            enc.anim(total_n, make)

    def run(self) -> RenderResult:
        t_start = time.time()
        opts, script = self.opts, self.script
        media.require_tools()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        work = self.out_dir / "_work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        self.build_scenes()
        opts.log(f"  {script.id}: 장면 {len(self.renderers)}개, 문장 {len(script.narration_sentences())}개 — TTS 준비")
        clips = self.synthesize()
        tbeats = self.timeline(clips)
        total = sum(tb.frames for tb in tbeats)
        fps = self.g.fps
        duration = total / fps
        painter = CaptionPainter(self.g, self.pal, self.fonts)
        cues = self.cues(tbeats, painter)
        burn_enabled = (self.cfg.video.model_extra or {}).get("burn_subtitles", True)
        burn = {i for i, r in enumerate(self.renderers) if burn_enabled and not r.own_captions}
        # 진행 막대 칸: 쇼츠는 장면, 롱폼은 챕터
        if script.format == "short":
            keyf = [tb.scene for tb in tbeats]
        else:
            keyf = [self.chapter_of[tb.scene] for tb in tbeats]
        units: list[tuple[int, int]] = []
        for tb, k in zip(tbeats, keyf):
            if not units or units[-1][2] != k:
                units.append([tb.start, 0, k])  # type: ignore[arg-type]
            units[-1][1] += tb.frames  # type: ignore[index]
        prog = Progress(self.g, self.pal, [(u[0], u[1]) for u in units], total,
                        enabled=(self.cfg.video.model_extra or {}).get("progress_bar", True))
        opts.log(f"  {script.id}: {duration:.1f}초 / {total}프레임 — 세그먼트 인코딩")
        enc = Encoder(work, self.g, opts.jobs or max(1, (os.cpu_count() or 2) // 2), opts.threads)
        try:
            self.encode(enc, tbeats, cues, burn, prog, painter)
        except BaseException:
            enc.pool.shutdown(wait=True, cancel_futures=True)
            raise
        segments = enc.finish()
        listing = work / "concat.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
        # 오디오
        narr = build_narration(tbeats, total, fps, work / "narration.wav")
        silent = self.tts is not None and self.tts.name == "silent" and not any(r.kind == "voice" for r in self.renderers)
        audio = work / "audio.m4a"
        ainfo = master_audio(narr, audio, duration, self.cfg, script.id, normalize=not silent)
        # 합치기
        video = self.out_dir / "video.mp4"
        tmp = self.out_dir / "video.part.mp4"
        media.ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                     "-c", "copy", "-movflags", "+faststart", "-metadata", f"title={script.title}", str(tmp), what="최종 합치기")
        tmp.replace(video)
        real = media.duration(video)
        if abs(real - duration) > 0.25:
            self.warnings.append(f"합친 영상 길이 {real:.2f}초가 타임라인 {duration:.2f}초와 다릅니다.")
        # 자막·썸네일·타임라인
        srt = write_srt(cues, self.out_dir / "subtitles.srt", self.fonts, painter.size, painter.max_w)
        from studio.video import thumbnail as TH

        if script.format == "longform":
            thumb_role, thumb = "thumbnail", TH.render_thumbnail(script, self.cfg, self.out_dir / "thumbnail.png")
        else:
            thumb_role, thumb = "cover", TH.render_cover(script, self.cfg, self.out_dir / "cover.png")
        if opts.preview:
            self._previews(tbeats, prog)
        chapters = self._chapters(tbeats)
        static_ratio = enc.still_frames / max(1, total)
        if script.format == "short" and duration > self.cfg.video.short_max_seconds:
            self.warnings.append(f"쇼츠 길이 {duration:.1f}초가 상한 {self.cfg.video.short_max_seconds}초를 넘습니다.")
        elapsed = time.time() - t_start
        tl = self._timeline_json(tbeats, cues, clips, duration, total, chapters, static_ratio, enc, ainfo, elapsed)
        tl_path = self.out_dir / "timeline.json"
        tl_path.write_text(json.dumps(tl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not opts.keep_work:
            shutil.rmtree(work, ignore_errors=True)
        human = sum(tb.frames for tb in tbeats if tb.beat.kind == "voice") / fps
        asset = RenderedAsset(
            asset_id=script.id,
            kind=script.format,
            files={"video": rel(video), thumb_role: rel(thumb), "subtitles": rel(srt), "timeline": rel(tl_path)},
            duration_seconds=round(real, 3),
            meta={
                "chapters": chapters,
                "scene_count": len(self.renderers),
                "static_ratio": round(static_ratio, 4),
                "tts_provider": self.tts.name if self.tts else None,
                "tts_voice": self.tts.voice if self.tts else None,
                "human_voice_seconds": round(human, 2),
                "size": [self.g.W, self.g.H],
                "variants": {r.scene.id: f"{r.kind}:{r.variant}" for r in self.renderers},
                "render_seconds": round(elapsed, 1),
            },
        )
        for r in self.renderers:
            r.release()
        return RenderResult(asset, self.out_dir, tl, elapsed, self.warnings)

    def _chapters(self, tbeats: list[TBeat]) -> list[list]:
        fps = self.g.fps
        out: list[list] = []
        seen = set()
        for tb in tbeats:
            ci = self.chapter_of[tb.scene]
            if ci not in seen:
                seen.add(ci)
                out.append([round(tb.start / fps, 2), self.script.chapters[ci].title])
        return out

    def _previews(self, tbeats: list[TBeat], prog: Progress) -> None:
        d = self.out_dir / "preview"
        d.mkdir(exist_ok=True)
        last: dict[int, TBeat] = {}
        for tb in tbeats:
            last[tb.scene] = tb
        for si, tb in last.items():
            r = self.renderers[si]
            im = r.still(tb.beat.step).copy()
            prog.paint(im, tb.start, True)
            im.convert("RGB").save(d / f"{si + 1:02d}_{r.scene.id}_{r.kind}{r.variant}.png")

    def _timeline_json(self, tbeats, cues, clips, duration, total, chapters, static_ratio, enc, ainfo, elapsed) -> dict:
        fps = self.g.fps
        scenes = []
        for si, r in enumerate(self.renderers):
            bs = [tb for tb in tbeats if tb.scene == si]
            scenes.append({
                "id": r.scene.id, "kind": r.kind, "variant": r.variant, "transition": r.transition,
                "chapter": self.chapter_of[si],
                "start": round(bs[0].start / fps, 3), "end": round((bs[-1].start + bs[-1].frames) / fps, 3),
                "beats": [{"kind": tb.beat.kind, "step": tb.beat.step, "line": tb.beat.line, "live": tb.beat.live,
                           "start": round(tb.start / fps, 3), "end": round((tb.start + tb.frames) / fps, 3)} for tb in bs],
            })
        sentences = []
        for tb in tbeats:
            if tb.beat.kind in ("speech", "voice") and tb.beat.line is not None:
                r = self.renderers[tb.scene]
                s = tb.start / fps
                sentences.append({
                    "scene": r.scene.id, "line": tb.beat.line, "text": D.strip_emphasis(r.scene.narration[tb.beat.line]),
                    "start": round(s, 3),
                    "end": round(s + (tb.clip_secs if tb.beat.kind == "speech" else tb.frames / fps), 3),
                    "audio": rel(tb.clip) if tb.clip else ("voice" if tb.beat.kind == "voice" else None),
                })
        return {
            "asset_id": self.script.id, "week": self.script.week, "format": self.script.format, "title": self.script.title,
            "fps": fps, "size": [self.g.W, self.g.H], "duration": round(duration, 3), "frames": total,
            "tts": {"provider": self.tts.name if self.tts else None, "voice": self.tts.voice if self.tts else None,
                    "rate": self.tts.rate if self.tts else None},
            "audio": ainfo,
            "chapters": chapters, "scenes": scenes, "sentences": sentences,
            "cues": [{"start": round(c.start, 3), "end": round(c.end, 3), "text": D.strip_emphasis(c.text)} for c in cues],
            "segments": {"count": len(enc.segments), "still_frames": enc.still_frames, "anim_frames": enc.anim_frames},
            "static_ratio": round(static_ratio, 4),
            "render_seconds": round(elapsed, 1),
            "warnings": self.warnings,
        }


def render_video(script: VideoScript, cfg: Config, opts: RenderOptions | None = None, out_dir: Path | None = None) -> RenderResult:
    opts = opts or RenderOptions()
    out_dir = out_dir or asset_out_dir(script.week, script.id)
    return VideoBuilder(script, cfg, opts, out_dir).run()
