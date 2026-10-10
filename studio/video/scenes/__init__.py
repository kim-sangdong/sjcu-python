"""장면 렌더러 레지스트리와 공통 엔진.

    from studio.video.scenes import scene, SceneRenderer

    @scene("bullets")
    class Bullets(SceneRenderer):
        required = ("items",)
        def build(self): ...          # self.add(sprite, x, y, show=단계) 로 요소를 놓는다

장면 하나는 '단계(step)'의 연속이다. 기본 규칙은 narration 문장 i → 단계 i 이고, 단계가 바뀔 때
새 요소가 나타나거나(rise/fade/pop/wipe/left) 기존 요소가 움직이고·흐려지고·모양이 바뀐다(Key).
그래서 문장이 진행될수록 화면이 하나씩 드러난다(정지 슬라이드쇼처럼 보이지 않게).

합성기(compose)는 plan() 이 돌려준 Beat 목록으로 시간을 배분한다.
  lead    장면 시작 직전의 짧은 전환(이전 장면에서 크로스페이드 또는 컷), 단계 -1(항상 보이는 요소만)
  speech  문장 하나(TTS 클립 + 0.25초 쉼)
  hold    내레이션 없는 시간(문제 풀이 카운트다운 등). live=True 면 매 프레임 live() 로 그린다
  voice   사람 녹음 구간(voice 장면). 녹음 파일 하나를 문장 수만큼 나눠 단계로 쓴다
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from studio.config import Config
from studio.models import Scene
from studio.video import design as D
from studio.video.design import Box, Fonts, Geometry, Palette, Sprite

REGISTRY: dict[str, type["SceneRenderer"]] = {}
SCENE_MODULES = ["basic", "quiz", "diagram", "media", "exam"]

LEAD_SECONDS = 0.35          # 장면 앞 전환 시간
FIRST_LEAD_SECONDS = 0.15    # 첫 장면은 짧게(쇼츠 첫 1초가 중요)
EXIT_SECONDS = 0.3


class SceneError(ValueError):
    pass


def scene(name: str) -> Callable[[type["SceneRenderer"]], type["SceneRenderer"]]:
    def deco(cls: type["SceneRenderer"]) -> type["SceneRenderer"]:
        cls.kind = name
        REGISTRY[name] = cls
        return cls
    return deco


def _load_all() -> None:
    for m in SCENE_MODULES:
        importlib.import_module(f"studio.video.scenes.{m}")


def available() -> list[str]:
    _load_all()
    return sorted(REGISTRY)


def get_renderer(kind: str) -> type["SceneRenderer"]:
    _load_all()
    cls = REGISTRY.get(kind)
    if cls is None:
        raise SceneError(f"알 수 없는 장면 kind '{kind}' 입니다. 사용 가능: {', '.join(available())}")
    return cls


# ---------------------------------------------------------------- 타임라인 단위

@dataclass
class Beat:
    kind: str                      # lead | speech | hold | voice
    step: int
    line: int | None = None        # narration 문장 번호(speech/voice)
    seconds: float = 0.0           # hold/voice 길이(speech 는 TTS 길이로 정해짐)
    live: bool = False             # 매 프레임 live() 로 그림
    sfx: str | None = None         # 'countdown' → 째깍 소리
    t0: float = 0.0                # voice: 녹음 안에서의 시작 초


@dataclass
class SceneContext:
    cfg: Config
    geo: Geometry
    pal: Palette
    fonts: Fonts
    asset_id: str
    fmt: str                       # longform | short
    index: int = 0                 # 영상 안에서 장면 순서
    chapter_title: str = ""
    chapter_index: int = 0
    motif: int = 0                 # 영상 단위 배경 무늬(asset_id 해시)
    root: Path = Path(".")
    prev: tuple[str, int] | None = None   # 직전 장면 (kind, variant) — 같은 모양 연속을 피한다
    channel_name: str = ""


# ---------------------------------------------------------------- 요소와 키프레임

@dataclass
class Key:
    """단계 step 부터 바뀌는 속성. 바뀌는 동안 delay 뒤 dur 초에 걸쳐 움직인다."""

    x: float | None = None
    y: float | None = None
    alpha: float | None = None
    sprite: Sprite | None = None
    delay: float = 0.0
    dur: float = 0.45


@dataclass
class Item:
    sp: Sprite
    x: float
    y: float
    show: int = 0                  # 이 단계부터 보인다(-1: 처음부터)
    hide: int | None = None        # 이 단계부터 사라진다
    enter: str = "rise"            # rise | fade | pop | wipe | wipe_down | grow_up | left | none
    delay: float = 0.0
    dur: float = 0.5
    keys: dict[int, Key] = field(default_factory=dict)
    z: int = 0
    name: str = ""

    def visible(self, step: int) -> bool:
        return self.show <= step and (self.hide is None or step < self.hide)

    def props(self, step: int) -> tuple[float, float, float, Sprite]:
        x, y, a, sp = self.x, self.y, 1.0, self.sp
        for k in sorted(self.keys):
            if k > step:
                break
            key = self.keys[k]
            x = key.x if key.x is not None else x
            y = key.y if key.y is not None else y
            a = key.alpha if key.alpha is not None else a
            sp = key.sprite if key.sprite is not None else sp
        return x, y, a, sp

    def key_between(self, a: int, b: int) -> Key | None:
        ks = [k for k in self.keys if a < k <= b]
        return self.keys[max(ks)] if ks else None


def _same(p: tuple, q: tuple) -> bool:
    return p[0] == q[0] and p[1] == q[1] and p[2] == q[2] and p[3] is q[3]


def _draw(canvas: Image.Image, sp: Sprite, x: float, y: float, alpha: float = 1.0) -> None:
    D.paste(canvas, sp.img, x - sp.ox, y - sp.oy, alpha)


# ---------------------------------------------------------------- 렌더러 기반

class SceneRenderer:
    kind: str = ""
    variants: int = 3
    required: tuple[str, ...] = ()
    own_captions: bool = False     # True 면 화면 자막을 굽지 않는다(장면이 직접 큰 글자로 보여 줌)

    def __init__(self, scene: Scene, ctx: SceneContext):
        self.scene = scene
        self.ctx = ctx
        self.g = ctx.geo
        self.pal = ctx.pal
        self.fonts = ctx.fonts
        self.p: dict[str, Any] = dict(scene.params)
        self.n = len(scene.narration)
        self.items: list[Item] = []
        self.warnings: list[str] = []
        self.validate()
        self.variant = self.choose_variant()
        self.transition = str(self.p.get("transition") or ("xfade" if D.pick(5, ctx.asset_id, scene.id, "tr") else "cut"))
        self.bg = self.background()
        self._stills: dict[int, Image.Image] = {}
        self._bases: dict[tuple, Image.Image] = {}
        self._changes: dict[tuple[int, int], dict[int, tuple[str, float]]] = {}
        self.build()
        self.items_sorted = sorted(enumerate(self.items), key=lambda t: (t[1].z, t[0]))

    # ---- 하위 클래스가 고치는 부분

    def validate(self) -> None:
        missing = [k for k in self.required if self.p.get(k) in (None, "", [])]
        if missing:
            raise SceneError(f"장면 {self.scene.id}({self.kind}): params 에 {', '.join(missing)} 가 필요합니다.")

    def build(self) -> None:
        raise NotImplementedError

    def plan(self) -> list[Beat]:
        """기본: lead → 문장마다 speech(단계 = line_step(i)) → hold_seconds."""
        beats = [Beat("lead", -1)]
        last = self.max_step()
        for i in range(self.n):
            beats.append(Beat("speech", min(self.line_step(i), last), line=i))
        hold = self.scene.hold_seconds
        if self.n == 0:
            hold = max(hold, 2.5)
        if hold > 0:
            beats.append(Beat("hold", last, seconds=hold))
        return beats

    def line_step(self, i: int) -> int:
        return i

    def live(self, canvas: Image.Image, beat: Beat, t: float, dur: float) -> None:
        """live 구간의 매 프레임 덧그리기(카운트다운·파형 등)."""

    def hold_audio(self, beat: Beat, seconds: float):
        """hold 구간 효과음(int16 배열) 또는 None."""
        return None

    # ---- 공통 도우미

    def choose_variant(self) -> int:
        v = self.p.get("variant")
        if isinstance(v, int) and not isinstance(v, bool):
            return v % self.variants
        v = D.pick(self.variants, self.ctx.asset_id, self.scene.id)
        if self.ctx.prev == (self.kind, v):
            v = (v + 1) % self.variants
        return v

    def warn(self, msg: str) -> None:
        self.warnings.append(f"장면 {self.scene.id}({self.kind}): {msg}")

    def add(self, sp: Sprite, x: float, y: float, *, show: int = 0, hide: int | None = None, enter: str = "rise",
            delay: float = 0.0, dur: float = 0.5, z: int = 0, name: str = "", keys: dict[int, Key] | None = None) -> Item:
        it = Item(sp, x, y, show=show, hide=hide, enter=enter, delay=delay, dur=dur, z=z, name=name, keys=keys or {})
        self.items.append(it)
        return it

    def text(self, text: str, *, role: str = "regular", size: int | None = None, color=None, max_w: int | None = None,
             **kw) -> D.TextBlock:
        tb = D.text_block(self.fonts, text, role=role, size=size or self.g.s.body, color=color or self.pal.ink,
                          max_w=max_w or self.g.content.w, **kw)
        if tb.overflow:
            self.warn(f"글자가 넘쳐 줄였습니다: '{D.strip_emphasis(text)[:24]}…' — 원고를 짧게 고치세요.")
        return tb

    def max_step(self) -> int:
        m = 0
        for it in self.items:
            m = max(m, it.show, *(k for k in it.keys))
            if it.hide is not None:
                m = max(m, it.hide)
        return m

    @staticmethod
    def spread(count: int, lines: int, lead: int = 0) -> list[int]:
        """count 개 요소의 공개 단계. 문장이 충분하면 문장마다 하나씩(lead 개 문장 뒤부터), 모자라면 고르게 묶는다."""
        if count <= 0:
            return []
        avail = max(1, lines - lead)
        if count <= avail:
            return [lead + j for j in range(count)]
        return [lead + (j * avail) // count for j in range(count)]

    def background(self) -> Image.Image:
        return make_background(self.ctx)

    def content(self) -> Box:
        return self.g.content

    # ---- 엔진: 정지 화면

    def still(self, step: int) -> Image.Image:
        im = self._stills.get(step)
        if im is None:
            im = self.bg.copy()
            for _, it in self.items_sorted:
                if it.visible(step):
                    x, y, a, sp = it.props(step)
                    _draw(im, sp, x, y, a)
            if len(self._stills) > 24:
                self._stills.clear()
            self._stills[step] = im
        return im

    # ---- 엔진: 단계 전환

    def _changing(self, a: int, b: int) -> dict[int, tuple[str, float]]:
        """a→b 에서 움직이는 요소 → (종류, 끝나는 시각)."""
        if (a, b) in self._changes:
            return self._changes[(a, b)]
        out: dict[int, tuple[str, float]] = {}
        for idx, it in enumerate(self.items):
            va, vb = it.visible(a), it.visible(b)
            if not va and vb:
                out[idx] = ("enter", it.delay + it.dur)
            elif va and not vb:
                out[idx] = ("exit", EXIT_SECONDS)
            elif va and vb:
                key = it.key_between(a, b)
                if key is not None and not _same(it.props(a), it.props(b)):
                    out[idx] = ("key", key.delay + key.dur)
        self._changes[(a, b)] = out
        return out

    def transition_seconds(self, a: int, b: int) -> float:
        if a == b:
            return 0.0
        ch = self._changing(a, b)
        return max((end for _, end in ch.values()), default=0.0)

    def frame(self, a: int, b: int, t: float) -> Image.Image:
        """a→b 전환이 시작된 지 t 초 뒤 화면."""
        if a == b:
            return self.still(b)
        ch = self._changing(a, b)
        if not ch or t >= max(end for _, end in ch.values()):
            return self.still(b)
        order = [idx for idx, _ in self.items_sorted]
        first = next(i for i, idx in enumerate(order) if idx in ch)
        bkey = (a, b, first)
        base = self._bases.get(bkey)
        if base is None:
            base = self.bg.copy()
            for idx in order[:first]:
                it = self.items[idx]
                if it.visible(b):
                    x, y, al, sp = it.props(b)
                    _draw(base, sp, x, y, al)
            if len(self._bases) > 8:
                self._bases.clear()
            self._bases[bkey] = base
        im = base.copy()
        for idx in order[first:]:
            it = self.items[idx]
            kind = ch.get(idx, (None, 0))[0]
            if kind is None:
                if it.visible(b):
                    x, y, al, sp = it.props(b)
                    _draw(im, sp, x, y, al)
            elif kind == "enter":
                self._draw_enter(im, it, b, t)
            elif kind == "exit":
                x, y, al, sp = it.props(a)
                _draw(im, sp, x, y, al * (1 - D.ease_out(t / EXIT_SECONDS)))
            else:
                key = it.key_between(a, b)
                q = D.ease_in_out((t - key.delay) / key.dur)
                xa, ya, aa, sa = it.props(a)
                xb, yb, ab, sb = it.props(b)
                x, y, al = xa + (xb - xa) * q, ya + (yb - ya) * q, aa + (ab - aa) * q
                if sa is sb:
                    _draw(im, sb, x, y, al)
                else:
                    if q < 1:
                        _draw(im, sa, x, y, al * (1 - q))
                    if q > 0:
                        _draw(im, sb, x, y, al * q)
        return im

    def _draw_enter(self, im: Image.Image, it: Item, step: int, t: float) -> None:
        x, y, al, sp = it.props(step)
        raw = (t - it.delay) / it.dur if it.dur > 0 else 1.0
        if raw <= 0:
            return
        p = D.ease_out(raw)
        e = it.enter
        if e == "none":
            _draw(im, sp, x, y, al)
        elif e == "fade":
            _draw(im, sp, x, y, al * p)
        elif e == "rise":
            _draw(im, sp, x, y + (1 - p) * self.g.unit * 1.6, al * D.clamp01(raw * 1.6))
        elif e == "left":
            _draw(im, sp, x - (1 - p) * self.g.unit * 2.5, y, al * D.clamp01(raw * 1.6))
        elif e == "pop":
            s = 0.82 + 0.18 * D.ease_back(raw)
            w, h = max(1, int(sp.img.width * s)), max(1, int(sp.img.height * s))
            img = sp.img.resize((w, h), Image.BILINEAR)
            cx, cy = x - sp.ox + sp.img.width / 2, y - sp.oy + sp.img.height / 2
            D.paste(im, img, cx - w / 2, cy - h / 2, al * D.clamp01(raw * 2))
        elif e == "grow_up":
            img = sp.img
            hh = max(1, int(round(img.height * p)))
            D.paste(im, img.crop((0, img.height - hh, img.width, img.height)), x - sp.ox, y - sp.oy + img.height - hh, al)
        elif e in ("wipe", "wipe_down"):
            img = sp.img
            if e == "wipe":
                cut = sp.ox + int(round(sp.w * p))
                part = img.crop((0, 0, max(1, min(img.width, cut + (sp.ox if p >= 1 else 0))), img.height))
            else:
                cut = sp.oy + int(round(sp.h * p))
                part = img.crop((0, 0, img.width, max(1, min(img.height, cut + (sp.oy if p >= 1 else 0)))))
            D.paste(im, part, x - sp.ox, y - sp.oy, al)
        else:
            _draw(im, sp, x, y, al * p)

    def release(self) -> None:
        self._stills.clear()
        self._bases.clear()


# ---------------------------------------------------------------- 배경

_BG_CACHE: dict[tuple, Image.Image] = {}


def make_background(ctx: SceneContext, *, fill: D.RGB | None = None, motif: int | None = None) -> Image.Image:
    """영상 단위 배경 무늬(motif): 0 둥근 빛 / 1 사선 띠 / 2 점 격자. 가장자리에만 옅게 둔다.

    같은 조건의 배경은 캐시해 공유한다 — 돌려받은 그림을 직접 고치지 말 것(copy() 후 그리기).
    """
    g, pal = ctx.geo, ctx.pal
    m = ctx.motif if motif is None else motif
    if fill is not None and fill != pal.bg:
        m = -1
    key = (g.W, g.H, g.portrait, m, fill, pal)
    hit = _BG_CACHE.get(key)
    if hit is None:
        if len(_BG_CACHE) > 16:
            _BG_CACHE.clear()
        hit = _BG_CACHE[key] = _draw_background(ctx, fill, m)
    return hit


def _draw_background(ctx: SceneContext, fill: D.RGB | None, m: int) -> Image.Image:
    g, pal = ctx.geo, ctx.pal
    W, H = g.W, g.H
    base = fill or pal.bg
    im = Image.new("RGBA", (W, H), base + (255,))
    if m == 0:
        d = int(min(W, H) * 0.95)
        orb = D.ellipse(d, d, D.rgba(pal.accent_soft, 0.55))
        D.paste(im, orb, W - d * 0.62, -d * 0.48)
        d2 = int(min(W, H) * 0.55)
        D.paste(im, D.ellipse(d2, d2, D.rgba(pal.primary_soft, 0.85)), -d2 * 0.45, H - d2 * 0.55)
    elif m == 1:
        # 오른쪽 위 모서리를 가로지르는 사선 띠 + 가는 선
        if g.portrait:
            band = [(0.55 * W, 0), (0.85 * W, 0), (W, 0.09 * H), (W, 0.27 * H)]
            thin = [(0.90 * W, 0), (0.93 * W, 0), (W, 0.025 * H), (W, 0.045 * H)]
        else:
            band = [(0.66 * W, 0), (0.84 * W, 0), (W, 0.30 * H), (W, 0.62 * H)]
            thin = [(0.88 * W, 0), (0.905 * W, 0), (W, 0.17 * H), (W, 0.215 * H)]
        layer = D.polygon(W, H, band, D.rgba(pal.accent_soft, 0.55))
        layer.alpha_composite(D.polygon(W, H, thin, D.rgba(pal.primary_soft, 1.0)))
        im.alpha_composite(layer)
    elif m == 2:
        step = max(28, int(min(W, H) * 0.032))
        r = max(2, step // 12)
        dots = D.blank(W, H)
        from PIL import ImageDraw
        dd = ImageDraw.Draw(dots)
        x0, y0 = int(W * 0.58), 0
        for yy in range(y0 + step // 2, int(H * 0.36), step):
            for xx in range(x0, W, step):
                fade = min(1.0, (xx - x0) / (W * 0.3)) * (1 - yy / (H * 0.36))
                if fade > 0.05:
                    dd.ellipse((xx - r, yy - r, xx + r, yy + r), fill=D.rgba(pal.primary, 0.16 * fade))
        im.alpha_composite(dots)
        bar_h = max(10, int(H * 0.012)) if not g.portrait else max(10, int(W * 0.012))
        if g.portrait:
            im.alpha_composite(Image.new("RGBA", (bar_h, H), D.rgba(pal.primary, 1.0)), (0, 0))
        else:
            im.alpha_composite(Image.new("RGBA", (W, bar_h), D.rgba(pal.primary, 1.0)), (0, H - bar_h))
    return im
