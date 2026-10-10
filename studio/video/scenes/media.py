"""그림·녹음 장면: image, voice.

image  {path(ROOT 기준), caption?}  그림 0, 설명 min(1, 마지막)
voice  {audio(ROOT 기준 사람 녹음 파일), heading?, image?, label?, cues?}
       TTS 대신 녹음 파일을 쓴다. narration 은 화면 큰 자막으로만 쓰고(문장 = 단계), 시간은 글자 수 비율로 나누거나
       cues(문장별 시작 초)를 따른다. 녹음의 실제 소리 크기로 움직이는 파형을 매 프레임 그린다(운영자 육성 = 사람 개입 신호).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from studio.video import design as D
from studio.video.design import Box
from studio.video.scenes import Beat, SceneError, SceneRenderer, scene
from studio.video.scenes.common import card, header, pill, vcenter

VOICE_GAP = 0.25


def load_picture(path: Path) -> Image.Image:
    try:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)
        return im.convert("RGBA")
    except Exception as e:  # 손상·형식 오류
        raise SceneError(f"이미지를 열지 못했습니다: {path} ({e})")


def fit_picture(im: Image.Image, w: int, h: int, radius: int) -> Image.Image:
    """비율을 지켜 상자 안에 맞추고(contain) 모서리를 둥글게."""
    s = min(w / im.width, h / im.height)
    nw, nh = max(1, int(im.width * s)), max(1, int(im.height * s))
    pic = im.resize((nw, nh), Image.LANCZOS)
    if radius:
        mask = D.rounded(nw, nh, radius, (255, 255, 255, 255)).getchannel("A")
        a = pic.getchannel("A")
        pic.putalpha(Image.fromarray(np.minimum(np.asarray(a), np.asarray(mask))))
    return pic


def _resolve(r: SceneRenderer, key: str) -> Path:
    raw = str(r.p.get(key) or "")
    p = Path(raw)
    p = p if p.is_absolute() else (r.ctx.root / p)
    if not p.exists():
        raise SceneError(f"장면 {r.scene.id}({r.kind}): {key} 파일이 없습니다 — {raw} (저장소 루트 기준 경로)")
    return p


@scene("image")
class ImageScene(SceneRenderer):
    required = ("path",)

    def validate(self) -> None:
        super().validate()
        self.path = _resolve(self, "path")

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        src = load_picture(self.path)
        cap = str(self.p.get("caption") or "")
        s_cap = max(0, min(1, self.n - 1))
        v = self.variant
        if v == 2 and not self.g.portrait:
            left, right = C.split_x(0.58, int(u * 3))
            pic = fit_picture(src, left.w, left.h, int(u * 0.8))
            sp = D.shadowed(pic, blur=20, offset=(0, 10), alpha=0.2)
            self.add(sp, left.x + (left.w - pic.width) / 2, left.y + (left.h - pic.height) / 2, enter="fade", dur=0.6)
            if cap:
                cb = self.text(cap, role="bold", size=S.h2, min_size=S.body_min, max_w=right.w - u * 2, max_h=right.h, lh=1.4,
                               emph=pal.primary)
                y = vcenter(cb.h, right)
                self.add(D.plain(D.rounded(max(8, u // 3), int(cb.h), 3, pal.accent)), right.x, y, show=s_cap, enter="wipe_down")
                self.add(cb, right.x + u * 1.6, y, show=s_cap, enter="left", delay=0.15)
            return
        if v == 1:
            pad = int(u * 1.1)
            cap_h = int(S.body * 1.36 * 2 + pad) if cap else 0
            pic = fit_picture(src, int(C.w * 0.86) - 2 * pad, int(C.h * 0.94) - 2 * pad - cap_h, 0)
            cb = self.text(cap, role="bold", size=S.body, min_size=S.body_min, max_w=pic.width, max_lines=2, align="center",
                           color=pal.ink, balance=True) if cap else None
            cap_h = (cb.h + pad) if cb else 0
            fw, fh = pic.width + 2 * pad, pic.height + 2 * pad + cap_h
            frame = D.rounded(fw, fh, int(u * 0.3), D.WHITE)
            D.paste(frame, pic, pad, pad)
            if cb:
                D.paste(frame, cb.img, (fw - cb.w) / 2 - cb.ox, pad + pic.height + pad * 0.6 - cb.oy)
            ang = -1.6 if D.pick(2, self.ctx.asset_id, self.scene.id, "tilt") else 1.4
            rot = frame.rotate(ang, expand=True, resample=Image.BICUBIC)
            sp = D.shadowed(rot, blur=22, offset=(0, 12), alpha=0.22)
            self.add(sp, C.cx - rot.width / 2, C.y + (C.h - rot.height) * 0.45, enter="pop", dur=0.6)
            return
        cb = self.text(cap, role="bold", size=S.body, min_size=S.body_min, max_w=int(C.w * 0.9), max_lines=3, align="center",
                       color=pal.ink, emph=pal.primary) if cap else None
        cap_h = (cb.h + u * 1.4) if cb else 0
        pic = fit_picture(src, C.w, C.h - cap_h, int(u * 0.8))
        y = vcenter(pic.height + cap_h, C, 0.4)
        self.add(D.shadowed(pic, blur=20, offset=(0, 10), alpha=0.2), C.cx - pic.width / 2, y, enter="fade", dur=0.6)
        if cb:
            self.add(cb, C.cx - cb.w / 2, y + pic.height + u * 1.4, show=s_cap, delay=0.3 if s_cap == 0 else 0)


@scene("voice")
class Voice(SceneRenderer):
    required = ("audio",)
    own_captions = True
    BARS = 32

    def validate(self) -> None:
        super().validate()
        self.audio_path = _resolve(self, "audio")
        self.image_path = _resolve(self, "image") if self.p.get("image") else None
        self.voice_seconds = 0.0
        self.env = np.zeros(1, dtype=np.float32)
        self._bars: dict[tuple[int, int], Image.Image] = {}

    def prepare_voice(self, seconds: float, samples: np.ndarray, sample_rate: int) -> None:
        """합성기가 녹음 길이와 소리 크기(프레임별 RMS)를 넘겨준다."""
        self.voice_seconds = seconds
        hop = max(1, sample_rate // self.g.fps)
        n = max(1, samples.size // hop)
        x = samples[: n * hop].astype(np.float32).reshape(n, hop) / 32768.0
        rms = np.sqrt((x ** 2).mean(axis=1))
        ref = float(np.percentile(rms, 95)) if rms.size else 0.0
        self.env = np.clip(rms / ref, 0, 1.3) if ref > 1e-4 else np.zeros_like(rms)

    def _cues(self) -> list[tuple[float, float]]:
        total = self.voice_seconds
        n = max(1, self.n)
        cues = self.p.get("cues")
        if isinstance(cues, list) and len(cues) == self.n and all(isinstance(c, (int, float)) for c in cues):
            starts = [max(0.0, min(total, float(c))) for c in cues]
            starts[0] = 0.0
        else:
            w = [max(1, len(re.sub(r"\s", "", s))) for s in self.scene.narration] or [1]
            acc, starts = 0.0, []
            for k in w:
                starts.append(total * acc / sum(w))
                acc += k
        ends = starts[1:] + [total]
        return [(s, max(0.2, e - s)) for s, e in zip(starts, ends)][:n]

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        cues = self._cues()
        for i, (s, d) in enumerate(cues):
            beats.append(Beat("voice", min(i, self.max_step()), line=i if self.n else None, seconds=d, live=True, t0=s))
        beats[-1].seconds += VOICE_GAP
        if self.scene.hold_seconds > 0:
            beats.append(Beat("hold", beats[-1].step, seconds=self.scene.hold_seconds))
        return beats

    def build(self) -> None:
        C, S, pal, u = self.content(), self.g.s, self.pal, self.g.unit
        v = self.variant
        label = str(self.p.get("label") or "직접 녹음")
        box = header(self, C)
        # 라벨(마이크 아이콘 + 글)
        lab = pill(self, label, size=S.small, fill=pal.primary, color=pal.on_primary)
        mic = D.ellipse(lab.height, lab.height, pal.accent)
        D.paste(mic, D.icon_mic(int(lab.height * 0.7), pal.ink), lab.height * 0.15, lab.height * 0.15)
        head = D.blank(lab.height + u // 2 + lab.width, lab.height)
        D.paste(head, mic, 0, 0)
        D.paste(head, lab, lab.height + u // 2, 0)
        heading = str(self.p.get("heading") or "")
        hb = self.text(heading, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2) if heading else None
        wave_h = int(S.body * 2.2)
        top_h = head.height + u + ((hb.h + u) if hb else 0)
        area = Box(box.x, int(box.y + top_h), box.w, int(box.h - top_h - wave_h - u))
        pic_box = None
        if self.image_path is not None:
            if self.g.portrait:
                ph = int(area.h * 0.42)
                pic_box, area = area.take_top(ph, u)
            else:
                pic_box, area = area.split_x(0.42, int(u * 2))
        self.add(D.plain(head), box.x if v != 0 else box.cx - head.width / 2, box.y, show=-1, enter="fade", dur=0.3)
        if hb:
            self.add(hb, box.x if v != 0 else box.cx - hb.w / 2, box.y + head.height + u, show=-1, enter="fade", delay=0.1)
        if pic_box is not None:
            pic = fit_picture(load_picture(self.image_path), pic_box.w, pic_box.h, int(u * 0.8))
            self.add(D.shadowed(pic, blur=16, offset=(0, 8), alpha=0.18), pic_box.cx - pic.width / 2, pic_box.cy - pic.height / 2,
                     show=-1, enter="fade", dur=0.5)
        lines = self.scene.narration or [""]
        pad = int(u * 1.6)
        align = "center" if v == 0 else "left"
        cw = area.w if v != 1 else area.w - u * 1.5
        size = int(S.h2 if self.g.portrait else S.h2 * 1.05)
        blocks = [self.text(t, role="bold", size=size, min_size=S.body_min, max_w=cw - 2 * pad, max_h=area.h - 2 * pad, lh=1.4,
                            align=align, emph=pal.primary, marker=pal.accent_soft if v == 2 else None) for t in lines]
        ch = int(max(b.h for b in blocks) + 2 * pad)
        cy = area.y + max(0, (area.h - ch) // 2)
        if v == 0:
            self.add(card(area.w, ch, fill=pal.surface, radius=u, border=pal.line, border_w=2), area.x, cy, show=-1, enter="fade", dur=0.3)
        elif v == 1:
            self.add(D.plain(D.rounded(max(10, u // 2), ch, 4, pal.primary)), area.x, cy, show=-1, enter="wipe_down", dur=0.4)
        for i, b in enumerate(blocks):
            x = area.x + pad + ((cw - 2 * pad - b.w) / 2 if align == "center" else 0) + (u * 1.5 if v == 1 else 0)
            y = cy + (ch - b.h) / 2
            self.add(b, x, y, show=i, hide=i + 1 if i + 1 < len(blocks) else None, enter="rise", dur=0.4)
        self.wave_box = Box(box.x, box.b - wave_h, box.w, wave_h)

    def live(self, canvas: Image.Image, beat: Beat, t: float, dur: float) -> None:
        if beat.kind != "voice":
            return
        pal, b = self.pal, self.wave_box
        n = self.BARS
        fps = self.g.fps
        bw = max(6, int(b.w / n * 0.45))
        span = b.w / n
        base_f = (beat.t0 + t) * fps
        for k in range(n):
            # 가운데에서 바깥으로 퍼지는 파형: 바깥 막대일수록 조금 앞선 소리
            dist = abs(k - (n - 1) / 2) / ((n - 1) / 2)
            f = int(base_f - dist * 6)
            e = float(self.env[f]) if 0 <= f < self.env.size else 0.0
            h = max(bw, int(b.h * (0.08 + 0.92 * e * (1 - 0.45 * dist))))
            x = b.x + k * span + (span - bw) / 2
            h -= h % 2
            key = (k, h)
            bar = self._bars.get(key)
            if bar is None:
                bar = self._bars[key] = D.rounded(bw, h, bw // 2, D.mix(pal.primary, pal.accent, dist))
            D.paste(canvas, bar, x, b.cy - h / 2)
