"""영상 디자인 공통: 색·글꼴·화면 규격·한국어 줄바꿈·그리기 도우미.

- 색은 cfg.brand 에서 오고, 연한 바탕색·보조 글자색은 섞어서 만든다.
- 글자 크기는 고령층 기준: 가로 본문 최소 48px, 세로 최소 56px.
- 세로(9:16)는 위 14%·아래 22% 를 플랫폼 UI 안전영역으로 비운다. 자막은 아래 안전영역 바로 위, 본문은 자막 위.
- 한국어 줄바꿈은 픽셀 폭을 재서 어절 단위로, 한 어절이 너무 길면 글자 단위로 끊는다.
- 강조: 문장 안의 **낱말** 은 강조색으로 그린다(낭독에서는 ** 를 지운다).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from studio.config import BrandCfg, font_path

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]
SS = 3  # 도형 안티에일리어싱용 초표본 배율


# ---------------------------------------------------------------- 색

def hex2rgb(h: str) -> RGB:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """t=0 → a, t=1 → b."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def rgba(c: RGB, alpha: float = 1.0) -> RGBA:
    return (c[0], c[1], c[2], int(round(255 * max(0.0, min(1.0, alpha)))))


def luminance(c: RGB) -> float:
    def ch(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(a: RGB, b: RGB) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


WHITE: RGB = (255, 255, 255)


@dataclass(frozen=True)
class Palette:
    bg: RGB
    ink: RGB
    primary: RGB
    accent: RGB
    muted: RGB
    surface: RGB        # 카드 바탕
    ink2: RGB           # 보조 글자
    ink3: RGB           # 흐린 글자
    primary_soft: RGB
    accent_soft: RGB
    line: RGB           # 구분선·트랙
    on_primary: RGB     # primary 위 글자
    accent_ink: RGB     # 바탕 위에서 읽히는 강조 글자색(accent 가 너무 밝으면 primary)
    correct: RGB
    shadow: RGB

    @classmethod
    def from_brand(cls, b: BrandCfg) -> "Palette":
        bg, ink, primary, accent, muted = (hex2rgb(x) for x in (b.background, b.text, b.primary, b.accent, b.muted))
        extra = b.model_extra or {}
        correct = hex2rgb(extra.get("correct", "#2F7A55"))
        on_primary = WHITE if contrast(WHITE, primary) >= contrast(ink, primary) else ink
        accent_ink = accent if contrast(accent, bg) >= 3.0 else primary
        return cls(
            bg=bg, ink=ink, primary=primary, accent=accent, muted=muted,
            surface=mix(bg, WHITE, 0.72),
            ink2=mix(ink, bg, 0.32),
            ink3=mix(ink, bg, 0.55),
            primary_soft=mix(primary, bg, 0.88),
            accent_soft=mix(accent, bg, 0.72),
            line=mix(muted, bg, 0.45),
            on_primary=on_primary,
            accent_ink=accent_ink,
            correct=correct,
            shadow=mix(ink, primary, 0.25),
        )


# ---------------------------------------------------------------- 글꼴

FontRole = Literal["regular", "bold", "heavy", "light"]


class Fonts:
    """역할별 글꼴. heavy=cfg.brand.font_bold(ExtraBold), bold=NanumGothicBold(없으면 heavy)."""

    def __init__(self, brand: BrandCfg):
        extra = brand.model_extra or {}
        self.files = {
            "regular": brand.font_regular,
            "heavy": brand.font_bold,
            "bold": extra.get("font_semibold", "NanumGothicBold.ttf"),
            "light": extra.get("font_light", "NanumGothicLight.ttf"),
        }
        self._paths: dict[str, str] = {}
        for role, name in self.files.items():
            try:
                self._paths[role] = str(font_path(name))
            except FileNotFoundError:
                if role in ("regular", "heavy"):
                    raise
        self._paths.setdefault("bold", self._paths["heavy"])
        self._paths.setdefault("light", self._paths["regular"])

    def get(self, role: str, size: int) -> ImageFont.FreeTypeFont:
        return _font(self._paths[role], int(size))


@lru_cache(maxsize=512)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


@lru_cache(maxsize=512)
def ink_center(font: ImageFont.FreeTypeFont) -> float:
    """'가' 글자 먹(ink)의 세로 중심(앵커 la 기준). 줄 상자 가운데에 한글이 오도록 맞출 때 쓴다."""
    _, top, _, bottom = font.getbbox("가")
    return (top + bottom) / 2


# ---------------------------------------------------------------- 상자·화면 규격

@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def r(self) -> int:
        return self.x + self.w

    @property
    def b(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    def inset(self, dx: int, dy: int | None = None) -> "Box":
        dy = dx if dy is None else dy
        return Box(self.x + dx, self.y + dy, max(1, self.w - 2 * dx), max(1, self.h - 2 * dy))

    def take_top(self, h: int, gap: int = 0) -> tuple["Box", "Box"]:
        h = min(h, self.h)
        return Box(self.x, self.y, self.w, h), Box(self.x, self.y + h + gap, self.w, max(1, self.h - h - gap))

    def split_x(self, ratio: float, gap: int = 0) -> tuple["Box", "Box"]:
        w1 = int((self.w - gap) * ratio)
        return Box(self.x, self.y, w1, self.h), Box(self.x + w1 + gap, self.y, self.w - w1 - gap, self.h)


@dataclass(frozen=True)
class Sizes:
    hero: int
    h1: int
    h2: int
    lead: int          # 큰 본문(목록·비교 등 글이 적을 때 시작 크기)
    body: int
    body_min: int
    small: int
    sub: int


@dataclass(frozen=True)
class Geometry:
    W: int
    H: int
    portrait: bool
    fps: int
    margin: int
    top_safe: int          # 세로: 위 UI 영역 높이(14%)
    bottom_safe: int       # 세로: 아래 UI 영역 높이(22%)
    progress: Box          # 진행 막대 트랙
    content: Box           # 장면 본문 영역(자막과 겹치지 않음)
    sub: Box               # 자막 영역(가운데 정렬, 아래 맞춤)
    s: Sizes
    unit: int              # 간격 기본 단위(px)

    @classmethod
    def make(cls, size: tuple[int, int], fps: int = 30) -> "Geometry":
        W, H = int(size[0]), int(size[1])
        portrait = H > W
        if portrait:
            k = W / 1080
            margin = int(72 * k)
            top_safe, bottom_safe = round(H * 0.14), round(H * 0.22)
            s = Sizes(hero=int(100 * k), h1=int(80 * k), h2=int(68 * k), lead=int(66 * k), body=int(58 * k), body_min=max(56, int(56 * k)),
                      small=int(44 * k), sub=max(56, int(58 * k)))
            sub_h = int(s.sub * 1.32 * 2 + 36 * k)
            sub_bottom = H - bottom_safe - int(18 * k)
            sub = Box(margin, sub_bottom - sub_h, W - 2 * margin, sub_h)
            progress = Box(margin, top_safe + int(16 * k), W - 2 * margin, max(6, int(8 * k)))
            top = progress.b + int(40 * k)
            content = Box(margin, top, W - 2 * margin, sub.y - int(36 * k) - top)
            unit = int(24 * k)
        else:
            k = H / 1080
            margin = int(120 * k)
            top_safe, bottom_safe = 0, 0
            s = Sizes(hero=int(120 * k), h1=int(84 * k), h2=int(66 * k), lead=int(62 * k), body=int(54 * k), body_min=max(48, int(48 * k)),
                      small=int(40 * k), sub=max(48, int(50 * k)))
            sub_h = int(s.sub * 1.3 * 2 + 30 * k)
            sub_bottom = H - int(40 * k)
            sub = Box(margin, sub_bottom - sub_h, W - 2 * margin, sub_h)
            progress = Box(margin, int(40 * k), W - 2 * margin, max(6, int(7 * k)))
            top = progress.b + int(44 * k)
            content = Box(margin, top, W - 2 * margin, sub.y - int(30 * k) - top)
            unit = int(24 * k)
        return cls(W=W, H=H, portrait=portrait, fps=fps, margin=margin, top_safe=top_safe, bottom_safe=bottom_safe,
                   progress=progress, content=content, sub=sub, s=s, unit=unit)


# ---------------------------------------------------------------- 결정적 선택

def stable_hash(*parts: object) -> int:
    return int(hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12], 16)


def pick(n: int, *parts: object) -> int:
    return stable_hash(*parts) % max(1, n)


# ---------------------------------------------------------------- 스프라이트

@dataclass
class Sprite:
    """그림 조각. (ox, oy) 는 그림 안에서 논리 상자의 왼쪽 위(그림자 여백 등 때문에 0이 아닐 수 있음)."""

    img: Image.Image
    w: int
    h: int
    ox: int = 0
    oy: int = 0
    meta: dict = field(default_factory=dict)


def blank(w: int, h: int) -> Image.Image:
    return Image.new("RGBA", (max(1, int(w)), max(1, int(h))), (0, 0, 0, 0))


def paste(dst: Image.Image, src: Image.Image, x: int, y: int, alpha: float = 1.0) -> None:
    """RGBA 합성. 화면 밖으로 나간 부분은 잘라 낸다."""
    if alpha <= 0.003:
        return
    x, y = int(round(x)), int(round(y))
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(src.width, dst.width - x), min(src.height, dst.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    part = src if (sx0, sy0, sx1, sy1) == (0, 0, src.width, src.height) else src.crop((sx0, sy0, sx1, sy1))
    if alpha < 0.997:
        part = with_alpha(part, alpha)
    dst.alpha_composite(part, (x + sx0, y + sy0))


@lru_cache(maxsize=64)
def _alpha_lut(a100: int) -> list[int]:
    return [v * a100 // 100 for v in range(256)]


def with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    out = img.copy()
    out.putalpha(img.getchannel("A").point(_alpha_lut(int(round(alpha * 100)))))
    return out


# ---------------------------------------------------------------- 도형(초표본 안티에일리어싱)

def _ss_draw(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = blank(w * SS, h * SS)
    return im, ImageDraw.Draw(im)


def _down(im: Image.Image, w: int, h: int) -> Image.Image:
    return im.resize((max(1, w), max(1, h)), Image.LANCZOS)


def rounded(w: int, h: int, r: int, fill: RGB | RGBA | None, *, outline: RGB | RGBA | None = None,
            width: int = 0) -> Image.Image:
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0, min(int(r), w // 2, h // 2))
    im, d = _ss_draw(w, h)
    d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=r * SS, fill=fill,
                        outline=outline, width=int(width * SS) if outline else 0)
    return _down(im, w, h)


def ellipse(w: int, h: int, fill: RGB | RGBA | None, *, outline: RGB | RGBA | None = None, width: int = 0) -> Image.Image:
    im, d = _ss_draw(w, h)
    inset = (width * SS) // 2 if outline else 0
    d.ellipse((inset, inset, w * SS - 1 - inset, h * SS - 1 - inset), fill=fill, outline=outline,
              width=int(width * SS) if outline else 0)
    return _down(im, w, h)


def polygon(w: int, h: int, pts: list[tuple[float, float]], fill: RGB | RGBA) -> Image.Image:
    im, d = _ss_draw(w, h)
    d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)
    return _down(im, w, h)


def polyline(w: int, h: int, pts: list[tuple[float, float]], color: RGB | RGBA, width: float) -> Image.Image:
    im, d = _ss_draw(w, h)
    sp = [(x * SS, y * SS) for x, y in pts]
    d.line(sp, fill=color, width=int(width * SS), joint="curve")
    rad = width * SS / 2
    for x, y in (sp[0], sp[-1]):
        d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=color)
    return _down(im, w, h)


def arc_ring(size: int, width: int, frac: float, color: RGB | RGBA, track: RGB | RGBA | None = None) -> Image.Image:
    """원형 진행 고리(위에서 시계 방향으로 frac 만큼)."""
    im, d = _ss_draw(size, size)
    box = (width * SS // 2, width * SS // 2, size * SS - width * SS // 2 - 1, size * SS - width * SS // 2 - 1)
    if track:
        d.ellipse(box, outline=track, width=width * SS)
    if frac > 0.001:
        d.arc(box, start=-90, end=-90 + 360 * min(1.0, frac), fill=color, width=width * SS)
    return _down(im, size, size)


def shadowed(img: Image.Image, *, blur: int = 18, offset: tuple[int, int] = (0, 8), color: RGB = (40, 20, 20),
             alpha: float = 0.16) -> Sprite:
    """부드러운 그림자를 붙인 스프라이트. 논리 상자는 원래 그림 자리."""
    pad = blur * 2 + max(abs(offset[0]), abs(offset[1]))
    W, H = img.width + 2 * pad, img.height + 2 * pad
    sh = Image.new("RGBA", (W, H), color + (0,))
    a = Image.new("L", (W, H), 0)
    a.paste(img.getchannel("A").point(lambda v: int(v * alpha)), (pad + offset[0], pad + offset[1]))
    a = a.filter(ImageFilter.GaussianBlur(blur))
    sh.putalpha(a)
    sh.alpha_composite(img, (pad, pad))
    return Sprite(sh, img.width, img.height, pad, pad)


def plain(img: Image.Image) -> Sprite:
    return Sprite(img, img.width, img.height, 0, 0)


# ---------------------------------------------------------------- 아이콘

def icon_check(size: int, color: RGB | RGBA, width: float | None = None) -> Image.Image:
    w = width or size * 0.14
    return polyline(size, size, [(size * 0.18, size * 0.54), (size * 0.42, size * 0.76), (size * 0.84, size * 0.28)], color, w)


def icon_cross(size: int, color: RGB | RGBA, width: float | None = None) -> Image.Image:
    w = width or size * 0.13
    a = polyline(size, size, [(size * 0.24, size * 0.24), (size * 0.76, size * 0.76)], color, w)
    a.alpha_composite(polyline(size, size, [(size * 0.76, size * 0.24), (size * 0.24, size * 0.76)], color, w))
    return a


def icon_arrow(length: int, thick: int, color: RGB | RGBA, direction: str = "right") -> Image.Image:
    """화살표(몸통+머리). direction: right | down."""
    head = thick * 2.6
    if direction == "right":
        w, h = length, int(head * 1.6)
        cy = h / 2
        im = blank(w, h)
        im.alpha_composite(polyline(w, h, [(thick, cy), (w - head * 0.9, cy)], color, thick))
        im.alpha_composite(polygon(w, h, [(w - head * 1.25, cy - head * 0.8), (w, cy), (w - head * 1.25, cy + head * 0.8)], color))
        return im
    im = icon_arrow(length, thick, color, "right")
    return im.transpose(Image.Transpose.ROTATE_270)


def icon_mic(size: int, color: RGB | RGBA) -> Image.Image:
    im, d = _ss_draw(size, size)
    S = size * SS
    d.rounded_rectangle((S * 0.36, S * 0.08, S * 0.64, S * 0.60), radius=S * 0.14, fill=color)
    d.arc((S * 0.24, S * 0.26, S * 0.76, S * 0.74), start=0, end=180, fill=color, width=int(S * 0.07))
    d.line((S * 0.5, S * 0.74, S * 0.5, S * 0.88), fill=color, width=int(S * 0.07))
    d.line((S * 0.34, S * 0.90, S * 0.66, S * 0.90), fill=color, width=int(S * 0.07))
    return _down(im, size, size)


# ---------------------------------------------------------------- 글자

_EMPH = re.compile(r"\*\*(.+?)\*\*")
NO_LINE_START = set(".,!?)]}」』》〉…·:;%~’”'\"")
SOFT_BREAK_AFTER = set("·/~,")       # 줄보다 긴 어절을 이 글자 뒤에서 먼저 나눈다(띄어쓰기 없는 나열)


def parse_emphasis(text: str) -> tuple[str, list[bool]]:
    """'**강조**' 표시를 지우고 글자별 강조 여부를 돌려준다. 여러 칸 공백은 한 칸으로."""
    text = re.sub(r"[ \t　]+", " ", str(text)).strip()
    text = re.sub(r" *\n *", "\n", text)
    out, mask, pos = [], [], 0
    for m in _EMPH.finditer(text):
        seg = text[pos:m.start()]
        out.append(seg)
        mask += [False] * len(seg)
        out.append(m.group(1))
        mask += [True] * len(m.group(1))
        pos = m.end()
    out.append(text[pos:])
    mask += [False] * len(text[pos:])
    plain_ = "".join(out)
    return plain_, mask


def strip_emphasis(text: str) -> str:
    return parse_emphasis(text)[0]


def wrap_spans(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[tuple[int, int]]:
    """어절 단위 줄바꿈. 반환: 줄마다 (시작, 끝) 글자 위치."""
    lines: list[tuple[int, int]] = []
    pos = 0
    for para in text.split("\n"):
        base = pos
        pos += len(para) + 1
        if not para.strip():
            lines.append((base, base))
            continue
        words = []
        for m in re.finditer(r"\S+", para):
            ws, we = m.start() + base, m.end() + base
            # 줄보다 긴 어절은 가운뎃점·빗금·물결·쉼표 뒤에서 먼저 나눈다(예: 도시·관리·농림·자연환경보전지역으로
            # 를 '…지역으/로' 처럼 글자 중간에서 자르지 않게 — 리허설 2026-W42 퀴즈 해설)
            if font.getlength(text[ws:we]) > max_w:
                s0 = ws
                for k in range(ws, we - 1):
                    if text[k] in SOFT_BREAK_AFTER:
                        words.append((s0, k + 1))
                        s0 = k + 1
                words.append((s0, we))
            else:
                words.append((ws, we))
        cur: tuple[int, int] | None = None
        for ws, we in words:
            if cur is not None and font.getlength(text[cur[0]:we]) <= max_w:
                cur = (cur[0], we)
                continue
            if cur is not None:
                lines.append(cur)
                cur = None
            if font.getlength(text[ws:we]) <= max_w:
                cur = (ws, we)
                continue
            # 한 어절이 줄보다 길면 글자 단위로 끊는다
            s = ws
            for i in range(ws + 1, we + 1):
                if font.getlength(text[s:i]) > max_w and i - 1 > s:
                    cut = i - 1
                    while cut < we and text[cut] in NO_LINE_START:   # 문장부호로 줄을 시작하지 않게
                        cut += 1
                    lines.append((s, cut))
                    s = cut
            cur = (s, we) if s < we else None
        if cur is not None:
            lines.append(cur)
    return lines


def balanced_spans(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[tuple[int, int]]:
    """같은 줄 수를 유지하면서 줄 길이를 고르게(제목용)."""
    spans = wrap_spans(text, font, max_w)
    n = len(spans)
    if n <= 1:
        return spans
    broke = _broke(text, spans)
    lo, hi = max_w * 0.45, max_w
    best = spans
    for _ in range(14):
        mid = (lo + hi) / 2
        cand = wrap_spans(text, font, mid)
        if len(cand) <= n and (broke or not _broke(text, cand)):
            best, hi = cand, mid
        else:
            lo = mid
    return best


@dataclass
class TextBlock(Sprite):
    size: int = 0
    lines: list[str] = field(default_factory=list)
    overflow: bool = False
    line_h: float = 0.0
    broke: bool = False     # 어절 중간에서 끊긴 줄이 있음(더 줄이는 편이 낫다)


def _broke(text: str, spans: list[tuple[int, int]]) -> bool:
    """어절 중간에서 끊긴 줄이 있는가(가운뎃점·빗금 등 뒤에서 나눈 것은 끊긴 것으로 보지 않는다)."""
    return any(e < len(text) and text[e] not in " \n" and e > s and text[e - 1] not in SOFT_BREAK_AFTER for s, e in spans[:-1])


def text_block(
    fonts: Fonts,
    text: str,
    *,
    role: str = "regular",
    size: int,
    color: RGB | RGBA,
    max_w: int,
    max_h: int | None = None,
    min_size: int | None = None,
    max_lines: int | None = None,
    align: str = "left",
    lh: float = 1.36,
    emph: RGB | RGBA | None = None,
    marker: RGB | RGBA | None = None,
    balance: bool = False,
    box_w: bool = False,
    stroke: tuple[int, RGB | RGBA] | None = None,
) -> TextBlock:
    """글자 상자를 그려 돌려준다. 넘치면 min_size 까지 줄이고, 그래도 넘치면 말줄임(overflow=True).

    box_w=True 면 그림 폭을 max_w 로 고정(정렬 기준 폭), 아니면 가장 긴 줄 폭.
    """
    plain_, mask = parse_emphasis(text)
    min_size = min_size or size
    sz = size
    while True:
        font = fonts.get(role, sz)
        spans = (balanced_spans if balance else wrap_spans)(plain_, font, max_w)
        L = sz * lh
        fits = (max_h is None or len(spans) * L <= max_h + 0.5) and (max_lines is None or len(spans) <= max_lines) \
            and not _broke(plain_, spans)
        if fits or sz <= min_size:
            break
        sz = max(min_size, sz - max(2, sz // 24))
    overflow = False
    limit = len(spans)
    if max_lines is not None:
        limit = min(limit, max_lines)
    if max_h is not None:
        limit = min(limit, max(1, int((max_h + 0.5) // L)))
    if limit < len(spans):
        overflow = True
        spans = spans[:limit]
        s, e = spans[-1]
        while e > s and font.getlength(plain_[s:e] + "…") > max_w:
            e -= 1
        spans[-1] = (s, e)
    lines = [plain_[s:e] + ("…" if overflow and i == len(spans) - 1 else "") for i, (s, e) in enumerate(spans)]
    widths = [font.getlength(t) for t in lines]
    content_w = int(math.ceil(max(widths) if widths else 1))
    W = max_w if box_w else max(1, content_w)
    H = max(1, int(math.ceil(len(lines) * L)))
    pad = max(6, sz // 6) + (stroke[0] if stroke else 0)
    img = blank(W + 2 * pad, H + 2 * pad)
    d = ImageDraw.Draw(img)
    c_off = ink_center(font)
    for i, ((s, e), line, lw) in enumerate(zip(spans, lines, widths)):
        if align == "center":
            x0 = (W - lw) / 2
        elif align == "right":
            x0 = W - lw
        else:
            x0 = 0.0
        y_center = i * L + L / 2
        y = y_center - c_off
        runs = _runs(mask[s:e] + ([False] if line.endswith("…") and overflow and i == len(lines) - 1 else []), line)
        x = x0
        for run_text, is_emph in runs:
            rw = font.getlength(run_text)
            if is_emph and marker is not None:
                mh = sz * 0.42
                d.rounded_rectangle((pad + x - sz * 0.06, pad + y_center + sz * 0.02, pad + x + rw + sz * 0.06, pad + y_center + sz * 0.02 + mh),
                                    radius=int(sz * 0.08), fill=marker)
            fill = emph if (is_emph and emph is not None) else color
            kw = {}
            if stroke:
                kw = {"stroke_width": stroke[0], "stroke_fill": stroke[1]}
            d.text((pad + x, pad + y), run_text, font=font, fill=fill, anchor="la", **kw)
            x += rw
    return TextBlock(img, W, H, pad, pad, size=sz, lines=lines, overflow=overflow, line_h=L, broke=_broke(plain_, spans))


def _runs(mask: list[bool], line: str) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    for ch, m in zip(line, mask + [False] * (len(line) - len(mask))):
        if out and out[-1][1] == m:
            out[-1] = (out[-1][0] + ch, m)
        else:
            out.append((ch, m))
    return out


def measure_lines(fonts: Fonts, text: str, role: str, size: int, max_w: int) -> int:
    plain_, _ = parse_emphasis(text)
    return len(wrap_spans(plain_, fonts.get(role, size), max_w))


# ---------------------------------------------------------------- 움직임 곡선

def clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def ease_out(t: float) -> float:
    t = clamp01(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    t = clamp01(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_back(t: float) -> float:
    """살짝 넘쳤다 돌아오는 곡선(강조용)."""
    t = clamp01(t)
    c1, c3 = 1.4, 2.4
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2
