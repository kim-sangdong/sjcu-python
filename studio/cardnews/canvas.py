"""브랜드 색·글꼴과 슈퍼샘플링 캔버스(카드뉴스·블로그 대표 이미지 공용).

도형 가장자리를 매끄럽게 하려고 scale 배로 크게 그린 뒤 마지막에 줄인다.
모든 좌표·크기 인자는 최종 이미지 기준 논리 픽셀이다.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageColor, ImageDraw

from studio.cardnews.text import TextBlock, fit_text, load_font, sanitize
from studio.config import Config, font_path

RGB = tuple[int, int, int]
JPEG_QUALITY = 92   # 인스타 캐러셀은 JPEG 만 받는다(facts-meta-tiktok-tistory.md 1.4)


def rgb(value: str) -> RGB:
    return ImageColor.getrgb(value)[:3]  # type: ignore[return-value]


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """a 에서 b 쪽으로 t(0~1)만큼 섞은 색."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def hex_of(c: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*c)


@dataclass(frozen=True)
class Brand:
    bg: RGB
    text: RGB
    primary: RGB
    accent: RGB
    muted: RGB
    font_regular: str
    font_bold: str
    font_medium: str
    white: RGB = (255, 255, 255)

    @property
    def primary_soft(self) -> RGB:
        return mix(self.primary, self.bg, 0.88)

    @property
    def accent_soft(self) -> RGB:
        return mix(self.accent, self.bg, 0.72)

    @property
    def text_soft(self) -> RGB:
        """보조 글자색(본문보다 옅게, 대비는 충분히)."""
        return mix(self.text, self.bg, 0.32)

    @property
    def on_primary(self) -> RGB:
        """진한 바탕(primary) 위 글자색."""
        return self.bg

    @property
    def fonts(self) -> tuple[str, str, str]:
        return (self.font_regular, self.font_medium, self.font_bold)


def brand_of(cfg: Config) -> Brand:
    b = cfg.brand
    medium = getattr(b, "font_medium", None) or (b.model_extra or {}).get("font_medium") or "NanumGothicBold.ttf"
    try:
        font_path(medium)
    except FileNotFoundError:
        medium = b.font_bold
    return Brand(
        bg=rgb(b.background),
        text=rgb(b.text),
        primary=rgb(b.primary),
        accent=rgb(b.accent),
        muted=rgb(b.muted),
        font_regular=b.font_regular,
        font_bold=b.font_bold,
        font_medium=medium,
    )


@lru_cache(maxsize=1)
def srgb_profile() -> bytes | None:
    """JPEG 에 넣을 sRGB ICC 프로필(없으면 None)."""
    try:
        from PIL import ImageCms

        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:  # littlecms 가 없는 Pillow
        return None


def save_png_jpeg(img: Image.Image, png: Path | None, jpg: Path | None) -> None:
    img = img.convert("RGB")
    if png:
        png.parent.mkdir(parents=True, exist_ok=True)
        img.save(png, "PNG", optimize=False, compress_level=6)
    if jpg:
        jpg.parent.mkdir(parents=True, exist_ok=True)
        kw = {"quality": JPEG_QUALITY, "subsampling": 0, "optimize": True}
        icc = srgb_profile()
        if icc:
            kw["icc_profile"] = icc
        buf = io.BytesIO()
        img.save(buf, "JPEG", **kw)   # 기본(baseline) JPEG — 확장 JPEG(MPO 등)는 인스타가 거부
        jpg.write_bytes(buf.getvalue())


class Canvas:
    def __init__(self, width: int, height: int, bg: RGB, *, scale: int = 2):
        self.w, self.h, self.s = width, height, scale
        self.img = Image.new("RGB", (width * scale, height * scale), bg)
        self.draw = ImageDraw.Draw(self.img)
        self.removed_chars: list[str] = []   # 글꼴에 없어 뺀 글자(경고용)

    def _p(self, *v: float) -> list[int]:
        return [round(a * self.s) for a in v]

    # ------------------------------------------------------------ 도형
    def rect(self, box: Sequence[float], *, fill=None, outline=None, width: float = 0, radius: float = 0) -> None:
        b = self._p(*box)
        w = round(width * self.s)
        if radius:
            self.draw.rounded_rectangle(b, radius=round(radius * self.s), fill=fill, outline=outline, width=w)
        else:
            self.draw.rectangle(b, fill=fill, outline=outline, width=w)

    def circle(self, cx: float, cy: float, r: float, *, fill=None, outline=None, width: float = 0) -> None:
        self.draw.ellipse(self._p(cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=round(width * self.s))

    def line(self, pts: Sequence[tuple[float, float]], *, fill, width: float) -> None:
        self.draw.line([tuple(self._p(x, y)) for x, y in pts], fill=fill, width=round(width * self.s), joint="curve")
        r = width / 2  # 끝을 둥글게
        for x, y in (pts[0], pts[-1]):
            self.circle(x, y, r, fill=fill)

    def polygon(self, pts: Sequence[tuple[float, float]], *, fill) -> None:
        self.draw.polygon([tuple(self._p(x, y)) for x, y in pts], fill=fill)

    def check(self, cx: float, cy: float, size: float, *, fill, width: float) -> None:
        """체크 표시(√ 모양)를 선으로 그린다(글꼴에 ✓ 가 없음)."""
        h = size / 2
        self.line([(cx - h * 0.85, cy + h * 0.02), (cx - h * 0.25, cy + h * 0.6), (cx + h * 0.9, cy - h * 0.6)], fill=fill, width=width)

    def arrow_up(self, cx: float, cy: float, size: float, *, fill) -> None:
        h = size / 2
        self.polygon([(cx, cy - h), (cx + h * 0.8, cy - h * 0.1), (cx - h * 0.8, cy - h * 0.1)], fill=fill)
        self.rect((cx - h * 0.26, cy - h * 0.2, cx + h * 0.26, cy + h), fill=fill)

    def dots(self, x: float, y: float, cols: int, rows: int, gap: float, r: float, *, fill) -> None:
        for i in range(cols):
            for j in range(rows):
                self.circle(x + i * gap, y + j * gap, r, fill=fill)

    # ------------------------------------------------------------ 글자
    def clean(self, text: str, fonts: Sequence[str]) -> str:
        out, removed = sanitize(text or "", fonts)
        self.removed_chars += removed
        return out

    def fit(self, text: str, *, font: str, emph_font: str | None = None, **kw) -> TextBlock:
        text = self.clean(text, [font, emph_font or font])
        return fit_text(text, font=font, emph_font=emph_font, scale=self.s, **kw)

    def put(self, block: TextBlock, x: float, y: float, width: float, *, color, emph_color=None, align: str = "left") -> float:
        return block.draw(self.draw, x, y, width=width, color=color, emph_color=emph_color, align=align)

    def text_width(self, text: str, font: str, size: int) -> float:
        return load_font(font, round(size * self.s)).getlength(text) / self.s

    def label(self, text: str, x: float, y: float, *, font: str, size: int, fill, anchor: str = "lm") -> float:
        """한 줄 글자(줄바꿈 없음). anchor 는 Pillow 규칙(lm = 왼쪽·세로 가운데). 글자 폭을 돌려준다."""
        text = self.clean(text, [font])
        f = load_font(font, round(size * self.s))
        self.draw.text(tuple(self._p(x, y)), text, font=f, fill=fill, anchor=anchor)
        return f.getlength(text) / self.s

    def pill(
        self, text: str, x: float, y: float, *, font: str, size: int, fill, color, pad_x: float = 0, height: float = 0, align: str = "left"
    ) -> tuple[float, float, float, float]:
        """둥근 알약 모양 이름표. (x, y)는 왼쪽 위(align=center 면 가운데 위). 상자 좌표를 돌려준다."""
        pad_x = pad_x or size * 0.75
        height = height or size * 1.9
        tw = self.text_width(self.clean(text, [font]), font, size)
        w = tw + pad_x * 2
        x0 = x - w / 2 if align == "center" else (x - w if align == "right" else x)
        box = (x0, y, x0 + w, y + height)
        self.rect(box, fill=fill, radius=height / 2)
        self.label(text, x0 + w / 2, y + height / 2, font=font, size=size, fill=color, anchor="mm")
        return box

    def finish(self) -> Image.Image:
        if self.s == 1:
            return self.img.copy()
        return self.img.resize((self.w, self.h), Image.Resampling.LANCZOS)
