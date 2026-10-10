"""시험 대비 장면: ox, table_blank, relations, calc, diff, ranking + 모든 장면 공통 근거 배지(params.basis).

원고 작성자용 표는 docs/SCENES.md. 여기 적은 규칙이 렌더러의 실제 동작이다.

근거 배지(params.basis) — 모든 kind 공통
  값: 문자열("[공인중개사법 제33조 제1항 · 2026.1.1 시행 기준]" 또는 대괄호 없는 글)
      | {law_name, article, effective_date}        → [공인중개사법 제33조 제1항 · 2026.1.1 시행 기준]
      | {case_no, decided_at, court?}              → [대법원 2019다12345 · 2020.3.12 선고] (사건 부호로 법원 추정)
      | {kind: "calc", note}                       → [계산 · note]
      | 위의 목록(최대 3개 표시)
  문자열 형식은 문제은행 Basis.badge() 와 같다. 시행일·선고일이 없으면 '시행일 미상'으로 그리고 경고를 남긴다.
  위치: 본문 영역(content) 맨 아래 띠. 장면 본문은 그 위로 줄어든다. 세로 영상에서는 자막 바로 위
        = 아래 안전영역(22%)·자막과 겹치지 않는다. 기존 장면은 단계 0 에 나타나고(flow 의 hidden 공개는 공개 단계),
        아래 새 장면은 정해진 단계에 나타난다. quiz 는 해설 상자 안에 같은 형식으로 쓴다.
  구현: 이 모듈을 불러오면 SceneRenderer.content()·__init__ 에 배지 처리를 끼운다(install_basis_hook).
        장면 클래스 속성 basis_inline=True(장면이 직접 그림)·basis_auto=False(장면이 place_basis 로 단계를 정함).

단계 배분(문장 수 N). 문장이 모자라 남는 단계는 조용히 보여 주고(hold), 문장이 남으면 마지막 단계에 머문다.
  ox           [훅 0] → 지문 → (timer 초 카운트다운: 막대+숫자+째깍) → 공개(R) → 교정/강조 → 해설·근거
               R = reveal_line → '정답'이 처음 나오는 문장(1번 이후) → hook 이 있고 N≥3 이면 2, 아니면 1 → N≤1 이면 없음.
               hook 이 있고 R≥2 이면 문장 0 은 훅만, 1..R-1 은 지문. 공개 뒤 문장은 [공개, 교정, 해설·근거] 에 차례로.
  table_blank  행 공개 단계(문장 N-B-H 개, 최소 1·최대 행 수) → 빈칸 B 개를 문장마다 하나씩 채움 → 강조(H=1).
               think>0 이면 빈칸마다 채우기 전에 think 초 동안 그 '?'가 깜박인다(째깍).
  relations    인물(노드) 0 → 관계 화살표(문장마다 하나, 모자라면 묶음) → 질문 → 결론(+근거·강조 인물).
  calc         조건 카드 0 → 수식 단계(문장마다 하나, 모자라면 묶음) → 정답 vs 흔한 오답 막대(+근거).
  diff         구법 0 → 신법 1 → 바뀐 부분 색 강조 2 → 시행일 타임라인 3. N 이 모자라면 구·신법을 묶고, 그다음 강조·타임라인을 묶는다.
  ranking      막대를 낮은 순위부터 하나씩(N-1 개 문장에 나눔) → focus 항목 확대 해설.
레이아웃 변형 3벌은 asset_id+scene.id 해시로 고른다(params.variant 로 강제). 가로는 좌우 2단, 세로는 위아래로 쌓는다.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any, Callable

from PIL import Image

from studio.video import design as D
from studio.video.design import Box, Sprite
from studio.video.scenes import Beat, Item, Key, SceneError, SceneRenderer, scene
from studio.video.scenes.common import badge, card, header, pill, vcenter
from studio.video.scenes.quiz import CountdownMixin, countdown_audio, read_seconds

# ================================================================ 근거 배지 문자열(영상·카드뉴스 공용)

_SUPREME_MARKS = {"다", "두", "도", "므", "마", "그", "스", "트", "모", "카", "추", "재다", "재두", "재도"}
_BADGE_RE = re.compile(r"^\[(.+?) · (.+)\]$")


def court_of(case_no: str) -> str:
    """사건번호의 사건 부호로 법원을 짐작한다(문제은행 bank.models.court_of 와 같은 규칙)."""
    m = re.match(r"^\s*\d{2,4}\s*([가-힣]{1,3})\s*\d", case_no or "")
    if not m:
        return ""
    mark = m.group(1)
    if mark.startswith("헌"):
        return "헌법재판소"
    return "대법원" if mark in _SUPREME_MARKS else ""


def parse_date(v: Any) -> date | None:
    """'2026-01-01'·'2026.1.1'·'2026. 1. 1.'·'20260101'·date → date. 알아볼 수 없으면 None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*[.일]?", s) or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def dot_date(v: Any) -> str:
    """날짜 → '2026.1.1'(알아볼 수 없으면 빈 글)."""
    d = parse_date(v)
    return f"{d.year}.{d.month}.{d.day}" if d else ""


def _squash(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("**", "")).strip()


def _as_list(value: Any) -> list[Any]:
    if value in (None, "", [], {}):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _basis_kind(b: dict) -> str:
    k = str(b.get("kind") or "")
    if k:
        return k
    if b.get("case_no"):
        return "precedent"
    if b.get("law_name") or b.get("article"):
        return "law"
    if b.get("note"):
        return "calc"
    return "text"


def basis_badge(b: Any) -> str:
    """근거 한 건 → 화면 배지 글. 형식은 문제은행 Basis.badge() 와 같다."""
    if b is None:
        return ""
    if isinstance(b, str):
        s = _squash(b)
        if not s:
            return ""
        return s if (s.startswith("[") and s.endswith("]")) else f"[{s}]"
    if not isinstance(b, dict):
        return basis_badge(str(b))
    kind = _basis_kind(b)
    if kind == "precedent":
        case_no = _squash(b.get("case_no"))
        court = _squash(b.get("court")) or court_of(case_no)
        d = dot_date(b.get("decided_at"))
        when = f"{d} {'결정' if court == '헌법재판소' else '선고'}" if d else "선고일 미상"
        return f"[{court + ' ' if court else ''}{case_no or '사건번호 미상'} · {when}]"
    if kind == "calc":
        note = _squash(b.get("note"))
        return f"[계산 · {note}]" if note else "[계산]"
    if kind == "law":
        parts = [_squash(b.get(k)) for k in ("law_name", "article") if _squash(b.get(k))]
        d = dot_date(b.get("effective_date"))
        return f"[{' '.join(parts)} · {d + ' 시행 기준' if d else '시행일 미상'}]"
    text = _squash(b.get("text") or b.get("label"))
    return basis_badge(text) if text else ""


def basis_badges(value: Any) -> list[str]:
    """params.basis(한 건 또는 목록) → 배지 글 목록."""
    return [t for t in (basis_badge(b) for b in _as_list(value)) if t]


def basis_problems(value: Any) -> list[str]:
    """화면에 그리기 전에 알려 줄 근거 문제(시행일·선고일 없음 등)."""
    out: list[str] = []
    for b in _as_list(value):
        if isinstance(b, str):
            continue
        if not isinstance(b, dict):
            out.append(f"근거 {b!r} 를 알아보지 못했습니다 — 문자열이나 {{law_name, article, effective_date}} 로 쓰세요.")
            continue
        kind = _basis_kind(b)
        if kind == "law":
            if not (_squash(b.get("law_name")) and _squash(b.get("article"))):
                out.append("근거(법령)에 law_name·article 이 비었습니다.")
            if not parse_date(b.get("effective_date")):
                out.append("근거(법령)에 시행일 effective_date(YYYY-MM-DD)가 없거나 형식이 틀려 '시행일 미상'으로 나옵니다.")
        elif kind == "precedent":
            if not parse_date(b.get("decided_at")):
                out.append("근거(판례)에 선고일 decided_at(YYYY-MM-DD)이 없거나 형식이 틀렸습니다.")
        elif kind == "text" and not _squash(b.get("text") or b.get("label")):
            out.append("근거 항목을 알아보지 못했습니다 — law_name·article·effective_date 또는 case_no·decided_at 를 쓰세요.")
    return out


# 화면 폭이 모자랄 때만 쓰는 법령 약칭(법제처 약칭). 배지 원문(문제은행·원고)은 정식 명칭 그대로 둔다.
LAW_SHORT: dict[str, str] = {
    "국토의 계획 및 이용에 관한 법률": "국토계획법",
    "공간정보의 구축 및 관리 등에 관한 법률": "공간정보관리법",
    "부동산 거래신고 등에 관한 법률": "부동산거래신고법",
    "부동산 실권리자명의 등기에 관한 법률": "부동산실명법",
    "집합건물의 소유 및 관리에 관한 법률": "집합건물법",
    "가등기담보 등에 관한 법률": "가등기담보법",
    "상가건물 임대차보호법": "상가임대차법",
    "주택임대차보호법": "주택임대차법",
    "도시 및 주거환경정비법": "도시정비법",
    "개발제한구역의 지정 및 관리에 관한 특별조치법": "개발제한구역법",
}


def shorten_badge(text: str) -> str:
    """배지 글의 긴 법령명을 약칭으로(긴 이름부터). 바꿀 것이 없으면 그대로."""
    for full in sorted(LAW_SHORT, key=len, reverse=True):
        if full in text:
            return text.replace(full, LAW_SHORT[full])
    return text


def badge_markup(text: str, *, split: bool = False) -> str:
    """배지 글의 출처 부분(법령명·조항, 사건번호)을 **강조** 로 감싼다. split=True 면 ' · ' 뒤에서 줄을 바꾼다."""
    m = _BADGE_RE.match(text)
    if not m:
        return text
    return f"[**{m.group(1)}** ·{chr(10) if split else ' '}{m.group(2)}]"


def exam_info(cfg) -> dict[str, Any]:
    """channel.yaml 의 exam 섹션(없으면 기본값). 문제은행 bank.exam.DEFAULT_EXAM 과 같은 기본값."""
    raw = (getattr(cfg, "model_extra", None) or {}).get("exam") or {}
    raw = raw if isinstance(raw, dict) else {}
    return {
        "name": str(raw.get("name") or "공인중개사"),
        "round": raw.get("round") or 37,
        "next_exam_date": parse_date(raw.get("next_exam_date") or "2026-10-31"),
    }


# ================================================================ 근거 배지 그리기와 장면 공통 연결

_UNSET = object()
_ORIG_CONTENT: Callable[[SceneRenderer], Box] = SceneRenderer.content


def basis_sprite(r: SceneRenderer, texts: list[str], max_w: int) -> Sprite:
    """근거 배지 띠(왼쪽 색 막대 + 배지 글 1~3줄 묶음). 한 줄에 안 들어가면 ' · ' 뒤에서 줄을 바꾼다."""
    S, pal = r.g.s, r.pal
    size = S.small
    padx, pady = int(size * 0.5), int(size * 0.3)
    bar = max(6, size // 7)
    gap_bar = int(size * 0.42)
    inner = max_w - 2 * padx - bar - gap_bar
    blocks: list[D.TextBlock] = []
    if len(texts) > 3:
        r.warn("근거가 4개 이상이라 앞의 3개만 화면에 넣었습니다.")
    for t in texts[:3]:
        cands = list(dict.fromkeys([t, shorten_badge(t)]))
        one = next((c for c in cands if D.measure_lines(r.fonts, badge_markup(c), "bold", size, inner) <= 1), None)
        if one is not None:
            tb = D.text_block(r.fonts, badge_markup(one), role="bold", size=size, color=pal.ink2, emph=pal.primary, max_w=inner,
                              max_lines=1, lh=1.28)
        else:
            tb = D.text_block(r.fonts, badge_markup(cands[-1], split=True), role="bold", size=size, min_size=int(size * 0.86),
                              color=pal.ink2, emph=pal.primary, max_w=inner, max_lines=3, lh=1.28)
            if tb.overflow:
                r.warn(f"근거 배지가 길어 줄였습니다: '{t[:30]}…'")
        blocks.append(tb)
    lg = int(size * 0.22)
    w = max(b.w for b in blocks) + 2 * padx + bar + gap_bar
    h = int(sum(b.h for b in blocks) + lg * (len(blocks) - 1) + 2 * pady)
    im = D.rounded(w, h, int(min(h / 2, size * 0.55)), pal.surface, outline=pal.line, width=2)
    D.paste(im, D.rounded(bar, max(4, h - 2 * pady), bar // 2, pal.primary), padx, pady)
    y = pady
    for b in blocks:
        D.paste(im, b.img, padx + bar + gap_bar - b.ox, y - b.oy)
        y += b.h + lg
    return D.shadowed(im, blur=10, offset=(0, 4), alpha=0.1)


def basis_gap(r: SceneRenderer) -> int:
    return int(r.g.unit * 0.9)


def basis_strip(r: SceneRenderer) -> Sprite | None:
    """이 장면이 본문 아래 띠에 그릴 근거 배지(없으면 None). 처음 부를 때 만들고 문제를 경고로 남긴다."""
    sp = r.__dict__.get("_basis_sp", _UNSET)
    if sp is _UNSET:
        sp = None
        if not getattr(r, "basis_inline", False):
            raw = r.p.get("basis")
            for msg in basis_problems(raw):
                r.warn(msg)
            texts = basis_badges(raw)
            if texts:
                sp = basis_sprite(r, texts, _ORIG_CONTENT(r).w)
        r.__dict__["_basis_sp"] = sp
    return sp


def inline_basis_text(r: SceneRenderer) -> str:
    """장면이 자기 상자 안에 근거를 쓸 때(quiz 해설 등) 쓸 배지 글. 문제는 경고로 남긴다(한 번만)."""
    if "_basis_inline_text" not in r.__dict__:
        raw = r.p.get("basis")
        for msg in basis_problems(raw):
            r.warn(msg)
        r.__dict__["_basis_inline_text"] = " ".join(basis_badges(raw))
    return r.__dict__["_basis_inline_text"]


def place_basis(r: SceneRenderer, show: int = 0, *, delay: float = 0.45) -> Item | None:
    """근거 배지를 본문 아래 띠(content() 가 비워 둔 자리)에 단계 show 부터 보이게 놓는다."""
    sp = basis_strip(r)
    if sp is None or r.__dict__.get("_basis_placed"):
        return None
    box = _ORIG_CONTENT(r)
    r.__dict__["_basis_placed"] = True
    return r.add(sp, box.x, box.b - sp.h, show=show, enter="left", delay=delay, dur=0.45, z=9, name="basis")


def install_basis_hook() -> None:
    """모든 장면이 params.basis 를 받게 SceneRenderer 에 연결한다(여러 번 불러도 한 번만).

    content(): 근거 배지 띠 높이만큼 본문 영역을 줄인다(basis_inline 장면 제외).
    __init__(): build 뒤 basis_auto 장면이면 배지를 단계 basis_step(기본 0)에 놓는다.
    """
    if getattr(SceneRenderer, "_basis_hook", False):
        return
    orig_init = SceneRenderer.__init__

    def content(self: SceneRenderer) -> Box:
        box = _ORIG_CONTENT(self)
        sp = basis_strip(self)
        if sp is None:
            return box
        return Box(box.x, box.y, box.w, max(1, box.h - sp.h - basis_gap(self)))

    def __init__(self: SceneRenderer, scene_, ctx) -> None:
        orig_init(self, scene_, ctx)
        if getattr(self, "basis_auto", True) and basis_strip(self) is not None and not self.__dict__.get("_basis_placed"):
            place_basis(self, show=int(getattr(self, "basis_step", 0)))
            self.items_sorted = sorted(enumerate(self.items), key=lambda t: (t[1].z, t[0]))

    SceneRenderer.content = content            # type: ignore[method-assign]
    SceneRenderer.__init__ = __init__          # type: ignore[method-assign]
    SceneRenderer._basis_hook = True           # type: ignore[attr-defined]


install_basis_hook()


# ================================================================ 공통 그리기 조각

DEFAULT_TIMER = 3.0
REVEAL_HOLD = 1.6
STEP_HOLD = 1.6


def fmt_num(v: float, unit: str = "") -> str:
    s = f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{s}{unit}"


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def q_chip(r: SceneRenderer, w: int, h: int, *, fill=None, color=None) -> Sprite:
    """빈칸 '?' 칩(표 빈칸·흐름도 숨김 단계)."""
    pal = r.pal
    w, h = max(8, int(w)), max(8, int(h))
    im = D.rounded(w, h, int(min(w, h) * 0.32), fill or pal.accent_soft, outline=D.mix(pal.accent, pal.primary, 0.35), width=3)
    tb = D.text_block(r.fonts, "?", role="heavy", size=max(10, int(h * 0.66)), color=color or pal.primary, max_w=w * 2, max_lines=1)
    D.paste(im, tb.img, (w - tb.w) / 2 - tb.ox, (h - tb.h) / 2 - tb.oy)
    return D.plain(im)


def ox_symbol(size: int, ok: bool, color, *, width: float | None = None) -> Image.Image:
    """O(고리) 또는 X(엇갈린 두 선)."""
    wd = width or size * 0.13
    if ok:
        inset = int(size * 0.1)
        im = D.blank(size, size)
        D.paste(im, D.ellipse(size - 2 * inset, size - 2 * inset, None, outline=color, width=int(wd)), inset, inset)
        return im
    a = D.polyline(size, size, [(size * 0.2, size * 0.2), (size * 0.8, size * 0.8)], color, wd)
    a.alpha_composite(D.polyline(size, size, [(size * 0.8, size * 0.2), (size * 0.2, size * 0.8)], color, wd))
    return a


def mark_color(r: SceneRenderer, ok: bool):
    return r.pal.correct if ok else r.pal.primary


def stamp(r: SceneRenderer, size: int, ok: bool, *, tilt: float = 0.0) -> Sprite:
    """채운 원 안의 흰 O/X(공개 도장)."""
    col = mark_color(r, ok)
    im = D.ellipse(size, size, col)
    ring = int(size * 0.07)
    D.paste(im, D.ellipse(size - 2 * ring, size - 2 * ring, None, outline=D.rgba(D.WHITE, 0.55), width=max(3, size // 40)), ring, ring)
    sym = int(size * 0.6)
    D.paste(im, ox_symbol(sym, ok, D.WHITE, width=size * 0.085), (size - sym) / 2, (size - sym) / 2)
    if tilt:
        im = im.rotate(tilt, expand=True, resample=Image.BICUBIC)
    sp = D.shadowed(im, blur=14, offset=(0, 7), alpha=0.22)
    return sp


def pulse(canvas: Image.Image, b: Box, t: float, color, u: int, *, radius: int | None = None) -> None:
    """상자 둘레로 퍼지며 사라지는 고리(1초 주기) — 생각할 시간 동안 '?'를 깜박이게."""
    ph = t % 1.0
    grow = D.ease_out(ph)
    e = int(u * 0.25 + u * 0.9 * grow)
    alpha = 0.85 * (1 - ph)
    if alpha <= 0.02:
        return
    w, h = b.w + 2 * e, b.h + 2 * e
    rad = (radius if radius is not None else int(min(b.w, b.h) * 0.32)) + e
    ring = D.rounded(w, h, rad, None, outline=D.rgba(color, 1.0), width=max(4, u // 5))
    D.paste(canvas, ring, b.x - e, b.y - e, alpha)


def arrow_sprite(p0: tuple[float, float], p1: tuple[float, float], color, thick: float, *, head: float | None = None,
                 dashed: bool = False) -> tuple[Sprite, float, float]:
    """p0 → p1 화살표 그림과 놓을 자리(왼쪽 위)."""
    head = head or thick * 3.0
    pad = head + thick * 2
    bx, by = min(p0[0], p1[0]) - pad, min(p0[1], p1[1]) - pad
    w, h = int(abs(p1[0] - p0[0]) + 2 * pad), int(abs(p1[1] - p0[1]) + 2 * pad)
    a = (p0[0] - bx, p0[1] - by)
    b = (p1[0] - bx, p1[1] - by)
    L = max(1e-6, math.hypot(b[0] - a[0], b[1] - a[1]))
    dx, dy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    end = (b[0] - dx * head * 0.9, b[1] - dy * head * 0.9)
    im = D.blank(w, h)
    if dashed:
        seg, gap = thick * 3.2, thick * 2.2
        pos, total = 0.0, max(0.0, L - head * 0.9)
        while pos < total:
            q = min(total, pos + seg)
            im.alpha_composite(D.polyline(w, h, [(a[0] + dx * pos, a[1] + dy * pos), (a[0] + dx * q, a[1] + dy * q)], color, thick))
            pos = q + gap
    else:
        im.alpha_composite(D.polyline(w, h, [a, end], color, thick))
    px, py = -dy, dx
    base = (b[0] - dx * head * 1.3, b[1] - dy * head * 1.3)
    im.alpha_composite(D.polygon(w, h, [(base[0] + px * head * 0.78, base[1] + py * head * 0.78), b,
                                        (base[0] - px * head * 0.78, base[1] - py * head * 0.78)], color))
    return D.plain(im), bx, by


def text_segments(r: SceneRenderer, tb: D.TextBlock, text: str, needle: str, *, role: str, align: str = "left") -> list[Box]:
    """글 상자 안에서 needle(첫 번째 나타남)이 차지하는 줄 조각들(글 상자 왼쪽 위 기준). 못 찾거나 말줄임이면 []."""
    plain_, _ = D.parse_emphasis(text)
    nd = D.strip_emphasis(needle)
    a = plain_.find(nd) if nd else -1
    if a < 0 or tb.overflow:
        return []
    b = a + len(nd)
    font = r.fonts.get(role, tb.size)
    pos, out = 0, []
    for i, line in enumerate(tb.lines):
        s = plain_.find(line, pos)
        if s < 0:
            return []
        e = s + len(line)
        pos = e
        lo, hi = max(a, s), min(b, e)
        if lo >= hi:
            continue
        lw = font.getlength(line)
        x0 = (tb.w - lw) / 2 if align == "center" else (tb.w - lw if align == "right" else 0.0)
        x = x0 + font.getlength(plain_[s:lo])
        w = font.getlength(plain_[lo:hi])
        out.append(Box(int(round(x)), int(round(i * tb.line_h)), max(1, int(round(w))), int(round(tb.line_h))))
    return out


def label_pill(r: SceneRenderer, text: str, *, fill, color, size: int | None = None) -> Sprite:
    return D.plain(pill(r, text, size=size or int(r.g.s.small * 0.9), fill=fill, color=color))


def note_card(r: SceneRenderer, text: str, w: int, max_h: float, *, label: str = "", accent=None, size: int | None = None) -> Sprite:
    """해설·결론 상자(왼쪽 색 막대 + 선택 이름표). 넘치면 글자를 줄이고 경고."""
    S, pal, u = r.g.s, r.pal, r.g.unit
    sz = size or S.body
    lab = pill(r, label, size=int(S.small * 0.88), fill=accent or pal.primary, color=D.WHITE) if label else None
    ov = lab.height // 2 if lab else 0
    pad = int(sz * 0.55)
    bar = max(8, sz // 7)
    tw = w - 2 * pad - bar - int(sz * 0.4)
    avail = max(sz * 1.4, max_h - ov - 2 * pad - (ov if lab else 0))
    tb = r.text(text, size=sz, min_size=min(sz, S.body_min), max_w=tw, max_h=avail, lh=1.36, color=pal.ink, emph=pal.primary)
    H = int(tb.h + 2 * pad + (ov if lab else 0))
    im = D.blank(w, H + ov)
    D.paste(im, D.rounded(w, H, int(sz * 0.3), pal.surface, outline=pal.line, width=2), 0, ov)
    D.paste(im, D.rounded(bar, max(6, H - 2 * pad), bar // 2, accent or pal.accent), pad * 0.5, ov + pad)
    if lab:
        D.paste(im, lab, pad, 0)
    D.paste(im, tb.img, pad * 0.5 + bar + int(sz * 0.4) - tb.ox, ov + (ov if lab else 0) + pad - tb.oy)
    return D.shadowed(im, blur=14, offset=(0, 6), alpha=0.12)


def walk(beats: list[Beat], lines: list[int], steps: list[int], hold_for: Callable[[int], float]) -> None:
    """문장 lines 를 단계 steps 에 차례로 하나씩. 문장이 남으면 마지막 단계에, 단계가 남으면 조용히 hold."""
    if not steps:
        return
    for j, i in enumerate(lines):
        beats.append(Beat("speech", steps[min(j, len(steps) - 1)], line=i))
    for st in steps[len(lines):]:
        beats.append(Beat("hold", st, seconds=hold_for(st)))


def merge_groups(units: list[str], target: int, order: list[str]) -> list[list[str]]:
    """단위 목록을 target 개 단계로 묶는다. order 순서대로 그 단위를 앞 단계에 합친다."""
    groups = [[u] for u in units]
    for name in order:
        if len(groups) <= max(1, target):
            break
        for gi in range(1, len(groups)):
            if groups[gi][0] == name:
                groups[gi - 1] += groups.pop(gi)
                break
    return groups


def _sfx_on(r: SceneRenderer) -> bool:
    return bool(r.ctx.cfg.video.model_extra.get("sfx", True))


# ================================================================ ox

@scene("ox")
class OX(CountdownMixin, SceneRenderer):
    """함정 OX: [훅] → 지문 카드 + 타이머 막대(실제 애니메이션·째깍) → O/X 공개 → (X) 틀린 말 취소선 + 정답 말 끼워 넣기
    / (O) right_word 형광펜 → 해설·근거 배지.

    params: statement, answer(true=O·맞는 지문 / false=X), wrong_word?, right_word?, timer?(초, 기본 3, 0이면 없음),
            hook?(첫 문장 훅 글), explanation?(짧은 해설), label?(지문 이름표, 기본 '지문'), reveal_line?, basis?
    변형: 0 카드 모서리 도장 + 결과 알약 / 1 O·X 큰 단추 두 개 / 2 진한 지문 카드 + 고리 타이머 → 큰 도장
    """

    required = ("statement",)
    basis_auto = False

    def validate(self) -> None:
        super().validate()
        a = self.p.get("answer")
        if isinstance(a, str) and a.strip().upper() in ("O", "X"):
            a = a.strip().upper() == "O"
        if not isinstance(a, bool):
            raise SceneError(f"장면 {self.scene.id}(ox): answer 는 true(O·맞는 지문) 또는 false(X·틀린 지문)여야 합니다.")
        self.answer: bool = a
        t = self.p.get("timer", DEFAULT_TIMER)
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not 0 <= t <= 30:
            raise SceneError(f"장면 {self.scene.id}(ox): timer 는 0~30 사이 초여야 합니다.")
        self.timer = float(t)

    # ---- 단계

    def _phases(self) -> None:
        N, lines = self.n, self.scene.narration
        self.hook = str(self.p.get("hook") or "").strip()
        R = self.p.get("reveal_line")
        if not (isinstance(R, int) and not isinstance(R, bool) and 1 <= R < N):
            R = next((i for i, s in enumerate(lines) if i >= 1 and "정답" in s), None)
            if R is None and N >= 2:
                R = min(N - 1, 2 if (self.hook and N >= 3) else 1)
        self.R = R
        self.pre = R if R is not None else N
        self.s_hook = 0
        self.s_stmt = 1 if (self.hook and self.pre >= 2) else 0
        self.s_rev = self.s_stmt + 1
        stmt_plain = D.strip_emphasis(str(self.p["statement"]))
        wrong = D.strip_emphasis(str(self.p.get("wrong_word") or ""))
        right = D.strip_emphasis(str(self.p.get("right_word") or ""))
        self.fix: str | None = None
        if not self.answer and wrong:
            if wrong in stmt_plain:
                self.fix = "strike"
            else:
                self.warn(f"wrong_word '{wrong}' 가 지문에 없어 교정 애니메이션을 뺐습니다.")
            if not right:
                self.warn("wrong_word 만 있고 right_word 가 없습니다 — 취소선만 긋습니다.")
        elif self.answer and right:
            if right in stmt_plain:
                self.fix = "marker"
            else:
                self.warn(f"right_word '{right}' 가 지문에 없어 강조를 뺐습니다.")
        self.wrong, self.right = wrong, right
        self.s_fix = self.s_rev + 1 if self.fix else None
        self.explanation = str(self.p.get("explanation") or "").strip()
        has_basis = bool(basis_badges(self.p.get("basis")))
        last = self.s_fix if self.s_fix is not None else self.s_rev
        self.s_basis = last + 1 if (self.explanation or has_basis) else None
        self.post_steps = [s for s in (self.s_rev, self.s_fix, self.s_basis) if s is not None]

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        for i in range(self.pre):
            beats.append(Beat("speech", self.s_hook if (i == 0 and self.s_stmt == 1) else self.s_stmt, line=i))
        if self.pre == 0:
            beats.append(Beat("hold", self.s_stmt, seconds=read_seconds(f"{self.hook} {self.p['statement']}")))
        if self.timer > 0:
            beats.append(Beat("hold", self.s_stmt, seconds=self.timer, live=True, sfx="countdown"))
        post = list(range(self.R, self.n)) if self.R is not None else []
        walk(beats, post, self.post_steps, self._hold_for)
        if self.scene.hold_seconds > 0:
            beats.append(Beat("hold", beats[-1].step, seconds=float(self.scene.hold_seconds)))
        return beats

    def _hold_for(self, st: int) -> float:
        if st == self.s_rev:
            return REVEAL_HOLD
        if st == self.s_fix:
            return self.fix_seconds + 1.0
        return read_seconds(f"{self.explanation} {' '.join(basis_badges(self.p.get('basis')))}")

    def live(self, canvas, beat, t, dur) -> None:
        if beat.sfx == "countdown":
            self.draw_countdown(canvas, t, dur)

    def hold_audio(self, beat, seconds):
        if beat.sfx == "countdown" and _sfx_on(self):
            return countdown_audio(seconds)
        return None

    # ---- 화면

    LH = 1.5          # 지문 줄 간격(교정 때는 그 줄부터 아래로 밀려 자리가 열린다)

    def build(self) -> None:
        self._phases()
        S, pal, u = self.g.s, self.pal, self.g.unit
        v = self.variant
        portrait = self.g.portrait
        C = self.content()
        box = C if self.hook else header(self, C)
        dark = v == 2
        lh = self.LH
        # 훅
        hb = None
        if self.hook:
            hb = self.text(self.hook, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2, emph=pal.primary, balance=True)
        st_sp = None
        if v == 0:
            tilt = -11 if D.pick(2, self.ctx.asset_id, self.scene.id, "tilt") else 9
            st_sp = stamp(self, int(S.hero * (1.25 if portrait else 1.3)), self.answer, tilt=tilt)
        gap_hook = int(u * 1.2) if st_sp is None else int(max(u * 1.2, st_sp.h * 0.45 + u * 0.4))
        top_h = (hb.h + gap_hook) if hb else (int(st_sp.h * 0.45) if st_sp is not None else 0)
        rest = Box(box.x, int(box.y + top_h), box.w, int(box.h - top_h))
        cd_h = int(S.h2 * (1.0 if portrait else 1.1))
        tile_h = int(S.hero * (1.2 if portrait else 1.6))
        ring = int(S.h1 * (1.75 if portrait else 2.4))
        res_h = self._result_pill().h
        area_h = {0: max(cd_h, res_h), 1: cd_h + u + tile_h, 2: ring if portrait else ring + u + res_h}[v]
        gap_area = int(u * 1.2)
        card_col = rest if portrait else rest.split_x(0.6, int(u * 2.5))[0]
        card_max = (rest.h - area_h - gap_area) if portrait else rest.h
        # 지문 카드 치수
        label = str(self.p.get("label") or "지문")
        lab = label_pill(self, label, fill=pal.accent if dark else pal.primary, color=pal.ink if dark else pal.on_primary)
        ov = lab.h // 2
        pad = int(u * 1.3)
        glyph_off = lambda sz: sz * lh / 2 - sz * 0.52          # noqa: E731 — 줄 상자 위에서 글자 윗부분까지
        top_pad = ov + int(u * 0.8)
        if st_sp is not None:                                   # 도장이 첫 줄 글자를 가리지 않게
            top_pad = max(top_pad, int(st_sp.h * 0.55 + 4 - glyph_off(S.lead)))
        slot = self._slot(S.lead, dark) if (self.fix == "strike" and self.right) else 0
        stmt = str(self.p["statement"])
        color, emph = (pal.on_primary, pal.accent) if dark else (pal.ink, pal.primary)
        tb = self.text(stmt, role="bold", size=S.lead, min_size=S.body_min, max_w=card_col.w - 2 * pad,
                       max_h=max(S.lead * lh, card_max - top_pad - pad - slot), lh=lh, color=color, emph=emph)
        if slot:
            slot = self._slot(tb.size, dark)
        ch = int(tb.h + top_pad + pad)
        # 배치
        if portrait:
            total = top_h + ch + slot + gap_area + area_h
            y0 = vcenter(total, box, 0.42)
            cx, cy = box.x, y0 + top_h
            ay = cy + ch + slot + gap_area
            area = Box(box.x, int(ay), box.w, int(box.b - ay))
        else:
            y0 = box.y
            left, area = rest.split_x(0.6, int(u * 2.5))
            cx, cy = left.x, vcenter(ch + slot, left, 0.4)
        if hb:
            self.add(hb, box.x, y0, show=self.s_hook, enter="left")
        fill = pal.primary if dark else (pal.primary_soft if v == 1 else pal.surface)
        col = mark_color(self, self.answer)

        def card_sp(h: int, revealed: bool) -> Sprite:
            if revealed:
                return card(card_col.w, h, fill=fill, radius=u, border=pal.accent if dark else col, border_w=6, shadow=v != 1,
                            shadow_alpha=0.18 if dark else 0.12)
            return card(card_col.w, h, fill=fill, radius=u, border=pal.line if v == 0 else None, border_w=2 if v == 0 else 0,
                        shadow=v != 1, shadow_alpha=0.18 if dark else 0.12)

        d_stmt = 0.35 if (self.s_stmt == self.s_hook and hb) else 0.0
        ckeys = {self.s_rev: Key(sprite=card_sp(ch, True), dur=0.35)}
        if slot and self.s_fix is not None:
            ckeys[self.s_fix] = Key(sprite=card_sp(ch + slot, True), dur=0.4)
        self.add(card_sp(ch, False), cx, cy, show=self.s_stmt, enter="rise", delay=d_stmt, z=0, keys=ckeys)
        self.add(lab, cx + pad, cy - ov, show=self.s_stmt, enter="pop", delay=d_stmt + 0.1, z=3)
        self.card_box = Box(int(cx), int(cy), card_col.w, ch)
        tx, ty = cx + pad, cy + top_pad
        if st_sp is not None:
            sy = ty + glyph_off(tb.size) - 4 - st_sp.h
            self.add(st_sp, cx + card_col.w - st_sp.w * 0.88, sy, show=self.s_rev, enter="pop", delay=0.05, dur=0.5, z=7)
        self.fix_seconds = 0.0
        lines = self._line_blocks(stmt, tb, color, emph)
        if lines is None:
            self.add(tb, tx, ty, show=self.s_stmt, enter="fade", delay=d_stmt + 0.15, dur=0.45, z=2)
            if self.fix:
                self.warn("지문이 말줄임 되어 교정 애니메이션을 뺐습니다 — 지문을 짧게 쓰세요.")
                self.fix = None
        else:
            self._place_lines(lines, tb, stmt, tx, ty, d_stmt, slot, fill, dark)
        # 공개·타이머·해설 영역
        if v == 0:
            self._area_result(area, cd_h)
        elif v == 1:
            self._area_tiles(area, cd_h, tile_h)
        else:
            self._area_ring(area, ring, res_h)
        if self.s_basis is not None:
            place_basis(self, show=self.s_basis, delay=0.3)

    def _insert_pill(self, size: int, dark: bool) -> Sprite:
        """정답 말 말풍선(아래 꼬리)."""
        pal = self.pal
        pf = max(int(self.g.s.small * 0.85), int(size * 0.7))
        tbw = D.text_block(self.fonts, self.right, role="heavy", size=pf, color=pal.correct if dark else D.WHITE,
                           max_w=self.g.content.w, max_lines=1, lh=1.15)
        px, py = int(pf * 0.45), int(pf * 0.16)
        tri = int(pf * 0.32)
        pw, ph = tbw.w + 2 * px, int(tbw.h + 2 * py)
        im = D.blank(pw, ph + tri)
        pcol = D.WHITE if dark else pal.correct
        D.paste(im, D.rounded(pw, ph, ph // 2, pcol), 0, 0)
        D.paste(im, D.polygon(tri * 2, tri, [(0, 0), (tri * 2, 0), (tri, tri)], pcol), pw / 2 - tri, ph - 1)
        D.paste(im, tbw.img, px - tbw.ox, py - tbw.oy)
        return D.shadowed(im, blur=10, offset=(0, 4), alpha=0.22)

    def _slot(self, size: int, dark: bool) -> int:
        """교정 말풍선이 들어갈 만큼 그 줄부터 아래를 미는 거리."""
        gap = size * self.LH - size * 1.0               # 위아래 줄 글자 사이 빈틈
        return max(0, int(math.ceil(self._insert_pill(size, dark).h - gap + size * 0.02)))

    def _line_blocks(self, text: str, tb: D.TextBlock, color, emph) -> list[D.TextBlock] | None:
        """여러 줄 글 상자를 줄마다 따로 그린다(교정 때 줄을 움직이려고). 말줄임이면 None."""
        if tb.overflow:
            return None
        plain_, mask = D.parse_emphasis(text)
        out, pos = [], 0
        for line in tb.lines:
            s = plain_.find(line, pos)
            if s < 0:
                return None
            e = s + len(line)
            pos = e
            mk, on = [], False
            for ch, m in zip(plain_[s:e], mask[s:e]):
                if m != on:
                    mk.append("**")
                    on = m
                mk.append(ch)
            if on:
                mk.append("**")
            out.append(D.text_block(self.fonts, "".join(mk), role="bold", size=tb.size, color=color, emph=emph,
                                    max_w=tb.w + tb.size * 2, max_lines=1, lh=self.LH))
        return out

    def _place_lines(self, lines: list[D.TextBlock], tb: D.TextBlock, stmt: str, tx: float, ty: float, d_stmt: float, slot: int,
                     fill, dark: bool) -> None:
        """지문 줄들 + (X) 그 줄부터 아래로 밀며 자리 열기 → 취소선 → 정답 말풍선 / (O) 형광펜."""
        pal, u = self.pal, self.g.unit
        L, size = tb.line_h, tb.size
        segs: list[Box] = []
        if self.fix:
            segs = text_segments(self, tb, stmt, self.wrong if self.fix == "strike" else self.right, role="bold")
            if not segs:
                self.warn("교정할 낱말의 자리를 찾지 못해 교정 애니메이션을 뺐습니다.")
                self.fix = None
        wl = int(round(segs[0].y / L)) if segs else 10 ** 6
        move = slot if (self.fix == "strike" and slot) else 0
        for i, ln in enumerate(lines):
            keys = {self.s_fix: Key(y=ty + i * L + move, dur=0.4)} if (move and i >= wl) else {}
            self.add(ln, tx, ty + i * L, show=self.s_stmt, enter="fade", delay=d_stmt + 0.15 + 0.08 * i, dur=0.45, z=2, keys=keys)
        if not self.fix:
            return
        s = self.s_fix
        if self.fix == "marker":
            for k, sg in enumerate(segs):
                hh = int(size * 0.5)
                mk = D.plain(D.rounded(sg.w + int(size * 0.16), hh, int(size * 0.1), D.rgba(pal.accent, 0.55)))
                self.add(mk, tx + sg.x - size * 0.08, ty + sg.y + sg.h / 2 + size * 0.02, show=s, enter="wipe", delay=0.1 + 0.4 * k,
                         dur=0.45, z=1)
            self.fix_seconds = 0.55 + 0.4 * len(segs)
            return
        th = max(5, int(size * 0.09))
        strike_col = pal.accent if dark else pal.primary
        t0 = 0.45 if move else 0.05
        t_end = t0
        for k, sg in enumerate(segs):
            d0 = t0 + 0.45 * k
            yy = ty + sg.y + move
            fade = D.plain(D.rounded(sg.w + 6, int(size * 1.02), 4, D.rgba(fill, 0.55)))
            self.add(fade, tx + sg.x - 3, yy + sg.h / 2 - size * 0.5, show=s, enter="fade", delay=d0 + 0.3, dur=0.3, z=3)
            line = D.plain(D.rounded(sg.w + int(size * 0.12), th, th // 2, strike_col))
            self.add(line, tx + sg.x - size * 0.06, yy + sg.h / 2 - th / 2, show=s, enter="wipe", delay=d0, dur=0.42, z=4)
            t_end = d0 + 0.6
        if self.right:
            sp = self._insert_pill(size, dark)
            sg = segs[0]
            glyph_top = ty + sg.y + move + sg.h / 2 - size * 0.48
            cb = self.card_box
            x = max(cb.x + u * 0.4, min(cb.r - u * 0.4 - sp.w, tx + sg.x + sg.w / 2 - sp.w / 2))
            self.add(sp, x, glyph_top + size * 0.12 - sp.h, show=s, enter="pop", delay=t_end, dur=0.45, z=6)
            t_end += 0.45
        self.fix_seconds = t_end

    def _result_pill(self) -> Sprite:
        S = self.g.s
        ok = self.answer
        txt = f"정답 {'O' if ok else 'X'} · {'맞는 지문' if ok else '틀린 지문'}"
        return label_pill(self, txt, fill=mark_color(self, ok), color=D.WHITE, size=int(S.body * 0.9))

    def _explain(self, area: Box, y: float, show: int, *, replace: list[Item] | None = None) -> None:
        if not self.explanation or show is None:
            return
        u = self.g.unit
        avail = area.b - y
        if replace is not None and avail < self.g.s.body * 2.6:
            for it in replace:
                it.keys[show] = Key(alpha=0.0, dur=0.3)
            y, avail = area.y, area.h
        sp = note_card(self, self.explanation, area.w, avail, label="해설")
        self.add(sp, area.x, y, show=show, enter="rise", delay=0.25, z=5)

    def _area_result(self, area: Box, cd_h: int) -> None:
        """변형 0: 타이머 막대 → 같은 자리에 결과 알약(도장은 카드 오른쪽 위) → 해설."""
        u = self.g.unit
        res = self._result_pill()
        row_y = area.y if self.g.portrait else vcenter(max(cd_h, res.h), area, 0.42)
        self.cd_box = Box(area.x, int(row_y + max(0, res.h - cd_h) / 2), area.w, cd_h)
        self.cd_style = "bar"
        it = self.add(res, area.x, row_y + max(0, cd_h - res.h) / 2, show=self.s_rev, enter="left", delay=0.35, z=5)
        self._explain(area, row_y + max(cd_h, res.h) + u, self.s_basis, replace=[it])

    def _area_tiles(self, area: Box, cd_h: int, tile_h: int) -> None:
        """변형 1: 타이머 막대 + O·X 큰 단추 → 정답 단추만 채우고 다른 단추는 흐리게 → 해설이 단추 자리에."""
        S, pal, u = self.g.s, self.pal, self.g.unit
        group = cd_h + u + tile_h
        y = area.y if self.g.portrait else vcenter(group, area, 0.42)
        self.cd_box = Box(area.x, int(y), area.w, cd_h)
        self.cd_style = "bar"
        ty = y + cd_h + u
        gap = int(u * 1.0)
        tw = (area.w - gap) // 2
        items: list[Item] = []
        for k, ok in enumerate((True, False)):
            sym = int(tile_h * 0.62)
            base = D.rounded(tw, tile_h, int(u * 0.8), pal.surface, outline=pal.line, width=3)
            D.paste(base, ox_symbol(sym, ok, D.mix(mark_color(self, ok), pal.bg, 0.15)), (tw - sym) / 2, (tile_h - sym) / 2)
            on = D.rounded(tw, tile_h, int(u * 0.8), mark_color(self, ok))
            D.paste(on, ox_symbol(sym, ok, D.WHITE), (tw - sym) / 2, (tile_h - sym) / 2)
            ic = int(tile_h * 0.26)
            D.paste(on, D.icon_check(ic, D.WHITE, width=ic * 0.16), tw - ic - u * 0.5, u * 0.4)
            correct = ok == self.answer
            keys = {self.s_rev: Key(sprite=D.shadowed(on, blur=14, offset=(0, 6), alpha=0.22), dur=0.35, delay=0.3)} if correct else \
                {self.s_rev: Key(alpha=0.25, dur=0.35)}
            it = self.add(D.shadowed(base, blur=12, offset=(0, 5), alpha=0.08), area.x + k * (tw + gap), ty, show=self.s_stmt, enter="rise",
                          delay=0.35 + 0.12 * k, keys=keys, z=1)
            items.append(it)
        self._explain(area, ty, self.s_basis, replace=items)

    def _area_ring(self, area: Box, ring: int, res_h: int) -> None:
        """변형 2: 고리 타이머 → 같은 자리에 큰 도장 + 결과 알약(세로는 옆, 가로는 아래) → 해설."""
        u = self.g.unit
        res = self._result_pill()
        st = stamp(self, int(ring * 1.05), self.answer)
        if self.g.portrait:
            row_w = ring + u + res.w
            x0 = area.x + max(0, (area.w - row_w) / 2)
            y = area.y
            self.cd_box = Box(int(x0), int(y), ring, ring)
            res_xy = (x0 + ring + u, y + (ring - res.h) / 2)
            below = y + ring + u
        else:
            group = ring + u + res.h
            y = vcenter(group, area, 0.42)
            self.cd_box = Box(area.x, int(y), area.w, ring)
            res_xy = (area.cx - res.w / 2, y + ring + u)
            below = y + ring + u + res.h + u
        self.cd_style = "ring"
        rc = (self.cd_box.x + self.cd_box.w / 2, self.cd_box.y + ring / 2)
        it1 = self.add(st, rc[0] - st.w / 2, rc[1] - st.h / 2, show=self.s_rev, enter="pop", delay=0.05, dur=0.5, z=6)
        it2 = self.add(res, *res_xy, show=self.s_rev, enter="left" if self.g.portrait else "rise", delay=0.35, z=5)
        self._explain(area, below, self.s_basis, replace=[it1, it2])


# ================================================================ table_blank

@scene("table_blank")
class TableBlank(SceneRenderer):
    """숫자 빈칸표: 제목·머리행 → 행이 차례로 등장(빈칸은 '?') → 문장 진행에 따라 빈칸 채움 → 헷갈리는 행 색 강조.

    params: columns[2~5], rows[[칸...], ...](각 행 칸 수 = columns), blanks?[[행, 열], ...](0부터, 채우는 순서),
            highlight_rows?[행, ...](0부터, 보통 2개), title?, think?(빈칸마다 채우기 전 생각할 초, 기본 0), basis?
    변형: 0 진한 머리행 + 줄무늬 / 1 행마다 카드 / 2 밑줄 머리행 + 첫 칸 이름표
    """

    required = ("columns", "rows")
    basis_auto = False

    def validate(self) -> None:
        super().validate()
        sid = self.scene.id
        cols, rows = self.p.get("columns"), self.p.get("rows")
        if not isinstance(cols, list) or not 2 <= len(cols) <= 5:
            raise SceneError(f"장면 {sid}(table_blank): columns 는 2~5개 목록이어야 합니다.")
        if not isinstance(rows, list) or not rows or any(not isinstance(r, list) or len(r) != len(cols) for r in rows):
            raise SceneError(f"장면 {sid}(table_blank): rows 는 [[칸, ...], ...] 이고 각 행의 칸 수가 columns({len(cols)}개)와 같아야 합니다.")
        if len(rows) > 10:
            raise SceneError(f"장면 {sid}(table_blank): 행이 {len(rows)}개입니다. 10개 이하로 나누세요.")
        self.blanks: list[tuple[int, int]] = []
        for b in self.p.get("blanks") or []:
            if not (isinstance(b, (list, tuple)) and len(b) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in b)
                    and 0 <= b[0] < len(rows) and 0 <= b[1] < len(cols)):
                raise SceneError(f"장면 {sid}(table_blank): blanks 의 {b!r} 는 [행, 열](0부터, 표 안) 이어야 합니다.")
            if tuple(b) not in self.blanks:
                self.blanks.append((b[0], b[1]))
        hl = self.p.get("highlight_rows") or []
        if not isinstance(hl, list) or any(not isinstance(x, int) or isinstance(x, bool) or not 0 <= x < len(rows) for x in hl):
            raise SceneError(f"장면 {sid}(table_blank): highlight_rows 는 행 번호(0부터) 목록이어야 합니다.")
        self.hl = list(dict.fromkeys(hl))
        th = self.p.get("think", 0)
        if not isinstance(th, (int, float)) or isinstance(th, bool) or not 0 <= th <= 10:
            raise SceneError(f"장면 {sid}(table_blank): think 는 0~10 초여야 합니다.")
        self.think = float(th)

    def _phases(self) -> None:
        M, B, H = len(self.p["rows"]), len(self.blanks), 1 if self.hl else 0
        rl = self.n - B - H
        r_count = max(1, min(M, rl))
        self.row_steps = self.spread(M, r_count)
        self.fill_steps = [r_count + k for k in range(B)]
        self.hl_step = r_count + B if H else None
        self.T = r_count + B + H

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        line = 0
        for st in range(self.T):
            if self.think > 0 and st in self.fill_steps:
                beats.append(Beat("hold", st - 1, seconds=self.think, live=True, sfx="think"))
            if line < self.n:
                beats.append(Beat("speech", st, line=line))
                line += 1
            else:
                beats.append(Beat("hold", st, seconds=2.2 if st == self.hl_step else STEP_HOLD))
        while line < self.n:
            beats.append(Beat("speech", self.T - 1, line=line))
            line += 1
        if self.scene.hold_seconds > 0:
            beats.append(Beat("hold", self.T - 1, seconds=float(self.scene.hold_seconds)))
        return beats

    def live(self, canvas, beat, t, dur) -> None:
        if beat.sfx != "think":
            return
        for k, st in enumerate(self.fill_steps):
            if st == beat.step + 1 and k < len(self.chip_boxes):
                pulse(canvas, self.chip_boxes[k], t, self.pal.accent, self.g.unit)

    def hold_audio(self, beat, seconds):
        if beat.sfx == "think" and _sfx_on(self):
            return countdown_audio(seconds)
        return None

    def build(self) -> None:
        self._phases()
        S, pal, u = self.g.s, self.pal, self.g.unit
        v = self.variant
        cols = [str(c) for c in self.p["columns"]]
        rows = [[str(c) for c in r] for r in self.p["rows"]]
        nC, nR = len(cols), len(rows)
        blank_at = {b: k for k, b in enumerate(self.blanks)}
        C = self.content()
        title = str(self.p.get("title") or "")
        box = C if title else header(self, C)        # 표는 자리가 귀해서 제목이 있으면 머리말(kicker)을 뺀다
        if title and self.g.portrait:
            tt = self.text(title, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2, emph=pal.primary)
            self.add(tt, box.x, box.y, show=0, enter="left")
            _, box = box.take_top(tt.h, int(tt.size * 0.6))
        elif title:                                   # 가로: 제목은 왼쪽 단, 표는 오른쪽 단(세로 자리를 표에 다 준다)
            left, box = box.split_x(0.27, int(u * 2.5))
            tt = self.text(title, role="heavy", size=S.h1, min_size=S.h2, max_w=left.w, max_lines=4, emph=pal.primary, balance=True)
            ty = vcenter(tt.h, left, 0.4)
            self.add(D.plain(D.rounded(max(8, u // 3), int(tt.h), 3, pal.accent)), left.x, ty, show=0, enter="wipe_down", dur=0.45)
            self.add(tt, left.x + u * 1.2, ty, show=0, enter="left")
        width = box.w
        rgap = int(u * 0.28) if v == 1 else 0
        min_sz = S.small
        sz = S.lead
        while True:
            hsz = max(min_sz, int(sz * 0.84))
            padx, pady = int(sz * 0.42), int(sz * 0.24)
            fh = self.fonts.get("bold", hsz)
            fb = self.fonts.get("bold", sz)
            nat = []
            for c in range(nC):
                wmax = fh.getlength(D.strip_emphasis(cols[c]))
                for r in range(nR):
                    wmax = max(wmax, fb.getlength(D.strip_emphasis(rows[r][c])))
                nat.append(max(wmax, sz * 1.6) + 2 * padx)
            total_nat = sum(nat)
            table_w = width if (self.g.portrait or total_nat > width) else min(width, int(max(total_nat * 1.3, width * 0.75)))
            widths = [int(table_w * n / total_nat) for n in nat]
            widths[-1] = table_w - sum(widths[:-1])
            heads = [D.text_block(self.fonts, cols[c], role="bold", size=hsz, max_w=widths[c] - 2 * padx, max_lines=2, align="center",
                                  color=pal.on_primary if v == 0 else (pal.ink2 if v == 1 else pal.primary), balance=True)
                     for c in range(nC)]
            cells = [[D.text_block(self.fonts, rows[r][c], role="bold" if c == 0 or (r, c) in blank_at else "regular", size=sz,
                                   max_w=widths[c] - 2 * padx, max_lines=2, align="center", balance=True,
                                   color=pal.primary if (r, c) in blank_at else pal.ink, emph=pal.primary)
                      for c in range(nC)] for r in range(nR)]
            head_h = int(max(h.h for h in heads) + 2 * pady)
            row_h = [int(max(max(cl.h for cl in cells[r]), sz * 1.25) + 2 * pady) for r in range(nR)]
            total = head_h + sum(row_h) + rgap * nR
            broke = any(cl.broke or cl.overflow for row in cells for cl in row) or any(h.broke for h in heads)
            if (total <= box.h and not broke) or sz <= min_sz:
                break
            sz -= 2
        if total > box.h:
            self.warn("표가 커서 화면을 넘칩니다 — 행이나 칸 글을 줄이세요(세로는 6행 안팎 권장).")
        elif sz < S.body_min:
            self.warn(f"표가 커서 칸 글자를 {sz}px 로 줄였습니다 — 행 수를 줄이면 더 크게 보입니다.")
        x0 = box.x if (self.g.portrait or title) else box.x + (box.w - table_w) / 2
        y0 = vcenter(total, box, 0.4)
        rad = int(u * 0.6)
        # 머리행
        if v == 0:
            self.add(D.plain(_top_rounded(table_w, head_h, rad, pal.primary)), x0, y0, show=0, enter="wipe", dur=0.45, z=0)
        elif v == 2:
            bar = max(6, u // 4)
            self.add(D.plain(D.rounded(table_w, bar, bar // 2, pal.accent)), x0, y0 + head_h - bar, show=0, enter="wipe", dur=0.45, z=0)
        x = x0
        for c in range(nC):
            h = heads[c]
            self.add(h, x + (widths[c] - h.w) / 2, y0 + (head_h - h.h) / 2, show=0, enter="fade", delay=0.15 + 0.05 * c, z=2)
            x += widths[c]
        # 행
        self.chip_boxes: list[Box] = [Box(0, 0, 1, 1)] * len(self.blanks)
        y = y0 + head_h + rgap
        cascade: dict[int, int] = {}
        hl_rank = {r: k for k, r in enumerate(self.hl)}
        for r in range(nR):
            st = self.row_steps[r]
            order = cascade.get(st, 0)
            cascade[st] = order + 1
            d0 = (0.25 if st == 0 else 0.05) + 0.14 * order
            rh = row_h[r]
            dim_keys = lambda: {self.hl_step: Key(alpha=0.35, dur=0.4)} if (self.hl_step is not None and r not in hl_rank) else {}
            if v == 0:
                if r % 2 == 1:
                    self.add(D.plain(D.rounded(table_w, rh, 0, D.mix(pal.surface, pal.primary_soft, 0.55))), x0, y, show=st, enter="fade",
                             delay=d0, dur=0.3, z=0)
                else:
                    self.add(D.plain(D.rounded(table_w, rh, 0, pal.surface)), x0, y, show=st, enter="fade", delay=d0, dur=0.3, z=0)
                if r == nR - 1:
                    self.add(D.plain(D.rounded(table_w, 3, 1, pal.primary)), x0, y + rh - 3, show=st, enter="wipe", delay=d0, z=1)
            elif v == 1:
                self.add(card(table_w, rh, fill=pal.surface, radius=rad, border=pal.line, border_w=2, shadow_alpha=0.07, blur=8), x0, y,
                         show=st, enter="left", delay=d0, z=0)
            else:
                self.add(D.plain(D.rounded(table_w, 2, 1, pal.line)), x0, y + rh - 1, show=st, enter="wipe", delay=d0, z=0)
                lw = widths[0] - padx
                self.add(D.plain(D.rounded(lw, rh - 2 * int(pady * 0.5), int(rh * 0.25), pal.primary_soft)), x0 + padx * 0.25,
                         y + pady * 0.5, show=st, enter="fade", delay=d0, z=0)
            if r in hl_rank and self.hl_step is not None:
                k = hl_rank[r]
                tone = (pal.accent, pal.primary)[k % 2]
                ov = D.rounded(table_w + 8, rh + 8, rad, D.rgba(D.mix(tone, pal.bg, 0.72), 0.85), outline=tone, width=4)
                self.add(D.plain(ov), x0 - 4, y - 4, show=self.hl_step, enter="wipe", delay=0.1 + 0.25 * k, dur=0.5, z=1)
            x = x0
            for c in range(nC):
                cl = cells[r][c]
                w = widths[c]
                cx_, cy_ = x + (w - cl.w) / 2, y + (rh - cl.h) / 2
                if (r, c) in blank_at:
                    k = blank_at[(r, c)]
                    fs = self.fill_steps[k]
                    cw_ = int(min(w - padx * 0.6, max(cl.w + padx, sz * 1.8)))
                    chh = int(min(rh - pady * 0.6, max(cl.h + pady * 0.4, sz * 1.3)))
                    chip_xy = (x + (w - cw_) / 2, y + (rh - chh) / 2)
                    self.chip_boxes[k] = Box(int(chip_xy[0]), int(chip_xy[1]), cw_, chh)
                    self.add(q_chip(self, cw_, chh), *chip_xy, show=st, enter="pop", delay=d0 + 0.08 * c, z=3,
                             keys={fs: Key(alpha=0.0, dur=0.25), **dim_keys()})
                    flash = D.plain(D.rounded(cw_, chh, int(chh * 0.3), D.rgba(pal.accent_soft, 0.9)))
                    self.add(flash, *chip_xy, show=fs, enter="fade", delay=0.05, dur=0.3, z=2, keys=dim_keys())
                    self.add(cl, cx_, cy_, show=fs, enter="pop", delay=0.2, dur=0.45, z=4, keys=dim_keys())
                else:
                    self.add(cl, cx_, cy_, show=st, enter="left", delay=d0 + 0.08 * c, z=2, keys=dim_keys())
                x += w
            y += rh + rgap
        place_basis(self, show=0, delay=0.6)


def _top_rounded(w: int, h: int, r: int, fill) -> Image.Image:
    im = D.rounded(w, h + r * 2, r, fill)
    return im.crop((0, 0, w, h))


# ================================================================ relations

_NODE_LAYOUT = {
    2: [(0.05, 0.5), (0.95, 0.5)],
    3: [(0.05, 0.1), (0.95, 0.1), (0.5, 0.92)],
    4: [(0.05, 0.08), (0.95, 0.08), (0.95, 0.92), (0.05, 0.92)],
    5: [(0.5, 0.0), (1.0, 0.38), (0.82, 1.0), (0.18, 1.0), (0.0, 0.38)],
    6: [(0.0, 0.1), (0.5, 0.1), (1.0, 0.1), (0.0, 0.9), (0.5, 0.9), (1.0, 0.9)],
}


@scene("relations")
class Relations(SceneRenderer):
    """판례 관계도: 인물·권리 노드 → 관계 화살표가 차례로 → 질문 → 결론(+근거 배지: 사건번호·선고일).

    params: nodes[{id, label, sub?, at?:[0~1, 0~1]}](2~6개), edges[{from, to, label?, dashed?}](1개 이상),
            question, conclusion, highlight?[노드 id](결론에서 강조), basis?(판례 {case_no, decided_at})
    변형: 0 흰 카드 노드 / 1 둥근 인물 노드(이름 아래 역할) / 2 진한 알약 노드
    """

    required = ("nodes", "edges", "question", "conclusion")
    basis_auto = False

    def validate(self) -> None:
        super().validate()
        sid = self.scene.id
        nodes, edges = self.p.get("nodes"), self.p.get("edges")
        if not isinstance(nodes, list) or not 2 <= len(nodes) <= 6 or any(not isinstance(n, dict) or "id" not in n or "label" not in n for n in nodes):
            raise SceneError(f"장면 {sid}(relations): nodes 는 [{{id, label}}, ...] 2~6개여야 합니다.")
        ids = [str(n["id"]) for n in nodes]
        if len(set(ids)) != len(ids):
            raise SceneError(f"장면 {sid}(relations): nodes 의 id 가 겹칩니다.")
        if not isinstance(edges, list) or not edges or any(not isinstance(e, dict) or str(e.get("from")) not in ids or str(e.get("to")) not in ids
                                                           or str(e.get("from")) == str(e.get("to")) for e in edges):
            raise SceneError(f"장면 {sid}(relations): edges 는 [{{from, to, label}}, ...] 이고 from·to 는 서로 다른 nodes 의 id 여야 합니다.")
        for n in nodes:
            at = n.get("at")
            if at is not None and not (isinstance(at, (list, tuple)) and len(at) == 2 and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in at)):
                raise SceneError(f"장면 {sid}(relations): 노드 {n['id']} 의 at 은 [0~1, 0~1] 이어야 합니다.")
        hl = self.p.get("highlight") or []
        if not isinstance(hl, list) or any(str(h) not in ids for h in hl):
            raise SceneError(f"장면 {sid}(relations): highlight 는 nodes 의 id 목록이어야 합니다.")

    def _phases(self) -> None:
        E, N = len(self.p["edges"]), self.n
        if N >= E + 3:
            self.edge_steps = [1 + k for k in range(E)]
            q = E + 1
        elif N >= 4:
            avail = N - 3
            self.edge_steps = [1 + s for s in self.spread(E, avail)]
            q = 1 + avail
        else:
            self.edge_steps = [0] * E
            q = 1
        self.s_q, self.s_c = q, q + 1
        self.T = q + 2

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        walk(beats, list(range(self.n)), list(range(self.T)), self._hold_for)
        if self.scene.hold_seconds > 0:
            beats.append(Beat("hold", self.T - 1, seconds=float(self.scene.hold_seconds)))
        return beats

    def _hold_for(self, st: int) -> float:
        if st == self.s_q:
            return read_seconds(str(self.p["question"]))
        if st == self.s_c:
            return read_seconds(f"{self.p['conclusion']} {' '.join(basis_badges(self.p.get('basis')))}")
        return STEP_HOLD

    def build(self) -> None:
        self._phases()
        S, pal, u = self.g.s, self.pal, self.g.unit
        v = self.variant
        C = self.content()
        box = header(self, C)
        q_text, c_text = str(self.p["question"]), str(self.p["conclusion"])
        if self.g.portrait:
            qw = box.w
            q_sp = self._qcard(q_text, qw, box.h * 0.26)
            c_sp = note_card(self, c_text, qw, box.h * 0.3, label="결론", accent=pal.correct, size=S.body)
            gap = int(u * 1.1)
            stacked = (box.h - q_sp.h - c_sp.h - 2 * gap) >= box.h * 0.4
            cards_h = (q_sp.h + gap + c_sp.h) if stacked else max(q_sp.h, c_sp.h)
            diag = Box(box.x, box.y, box.w, int(box.h - cards_h - gap))
            qy = diag.b + gap
            q_xy, c_xy = (box.x, qy), (box.x, qy + q_sp.h + gap if stacked else qy)
        else:
            diag, right = box.split_x(0.58, int(u * 3))
            q_sp = self._qcard(q_text, right.w, right.h * 0.42)
            c_sp = note_card(self, c_text, right.w, right.h * 0.52, label="결론", accent=pal.correct, size=S.body)
            gap = int(u * 1.2)
            stacked = q_sp.h + gap + c_sp.h <= right.h
            tot = (q_sp.h + gap + c_sp.h) if stacked else max(q_sp.h, c_sp.h)
            yy = vcenter(tot, right, 0.42)
            q_xy, c_xy = (right.x, yy), (right.x, yy + q_sp.h + gap if stacked else yy)
        qkeys = {} if stacked else {self.s_c: Key(alpha=0.0, dur=0.3)}
        self.add(q_sp, *q_xy, show=self.s_q, enter="rise", delay=0.05, z=4, keys=qkeys)
        self.add(c_sp, *c_xy, show=self.s_c, enter="rise", delay=0.3 if not stacked else 0.1, z=5)
        self._diagram(diag, v)
        place_basis(self, show=self.s_c, delay=0.6)

    def _qcard(self, text: str, w: int, max_h: float) -> Sprite:
        S, pal, u = self.g.s, self.pal, self.g.unit
        sz = S.lead
        qb = int(sz * 1.1)
        pad = int(u * 1.0)
        tb = self.text(text, role="heavy", size=sz, min_size=S.body_min, max_w=w - 2 * pad - qb - u, max_h=max(sz * 1.3, max_h - 2 * pad),
                       lh=1.3, emph=pal.primary, balance=True)
        h = int(max(qb, tb.h) + 2 * pad)
        im = D.rounded(w, h, u, pal.primary_soft)
        D.paste(im, badge(self, "Q", qb, fill=pal.primary, color=pal.on_primary), pad, (h - qb) / 2)
        D.paste(im, tb.img, pad + qb + u - tb.ox, (h - tb.h) / 2 - tb.oy)
        return D.plain(im)

    def _node_sprites(self, n: dict, v: int, sz: int, max_w: int, hl: bool) -> tuple[Sprite, tuple[str, float, float]]:
        """노드 그림과 모양((rect, 반폭, 반높이) 또는 (circle, 반지름, 0))."""
        S, pal, u = self.g.s, self.pal, self.g.unit
        label, sub = str(n["label"]), str(n.get("sub") or "")
        if v == 1:
            d = int(max(S.hero * 1.05, min(max_w, D.text_block(self.fonts, label, role="heavy", size=sz, color=pal.ink, max_w=max_w * 2,
                                                                            max_lines=1).w + sz * 0.9)))
            tb = D.text_block(self.fonts, label, role="heavy", size=sz, color=D.WHITE if hl else pal.primary, max_w=d - int(sz * 0.5),
                              max_lines=2, align="center", balance=True)
            sb = D.text_block(self.fonts, sub, role="bold", size=S.small, color=pal.ink2, max_w=max_w, max_lines=1, align="center") if sub else None
            W = max(d, sb.w if sb else 0)
            H = d + ((int(S.small * 0.3) + sb.h) if sb else 0)
            im = D.blank(W, H)
            circ = D.ellipse(d, d, pal.correct if hl else pal.surface, outline=pal.correct if hl else pal.primary, width=max(4, d // 22))
            D.paste(im, circ, (W - d) / 2, 0)
            D.paste(im, tb.img, (W - tb.w) / 2 - tb.ox, (d - tb.h) / 2 - tb.oy)
            if sb:
                D.paste(im, sb.img, (W - sb.w) / 2 - sb.ox, d + int(S.small * 0.3) - sb.oy)
            sp = D.shadowed(im, blur=12, offset=(0, 5), alpha=0.12)
            sp.meta["anchor"] = d / 2          # 원 중심의 세로 위치(그림 위 기준)
            return sp, ("circle", d / 2 + u * 0.3, 0)
        fg = D.WHITE if (v == 2 or hl) else pal.ink
        tb = D.text_block(self.fonts, label, role="heavy", size=sz, color=fg, max_w=max_w - int(sz * 0.9), max_lines=2, align="center", balance=True)
        sb = D.text_block(self.fonts, sub, role="bold", size=S.small, color=D.mix(fg, pal.primary, 0.25) if v == 2 or hl else pal.ink2,
                          max_w=max_w - int(sz * 0.9), max_lines=1, align="center") if sub else None
        padx, pady = int(sz * 0.45), int(sz * 0.3)
        W = int(max(tb.w, sb.w if sb else 0, sz * 1.6) + 2 * padx)
        H = int(tb.h + ((sb.h) if sb else 0) + 2 * pady)
        if v == 2:
            fill, outline, bw = (pal.correct if hl else pal.primary), None, 0
            rad = H // 2
        else:
            fill = D.mix(pal.correct, D.WHITE, 0.0) if hl else pal.surface
            outline, bw, rad = (pal.correct if hl else pal.primary), 4, int(u * 0.6)
        im = D.rounded(W, H, rad, fill, outline=outline, width=bw)
        D.paste(im, tb.img, (W - tb.w) / 2 - tb.ox, pady - tb.oy)
        if sb:
            D.paste(im, sb.img, (W - sb.w) / 2 - sb.ox, pady + tb.h - sb.oy)
        sp = D.shadowed(im, blur=12, offset=(0, 5), alpha=0.14)
        sp.meta["anchor"] = H / 2
        return sp, ("rect", W / 2 + u * 0.3, H / 2 + u * 0.3)

    def _diagram(self, area: Box, v: int) -> None:
        S, pal, u = self.g.s, self.pal, self.g.unit
        nodes = [dict(n, id=str(n["id"])) for n in self.p["nodes"]]
        edges = self.p["edges"]
        hl = {str(h) for h in (self.p.get("highlight") or [])}
        n = len(nodes)
        sz = S.lead if n <= 3 else S.body
        max_w = int(area.w * (0.42 if n <= 4 else 0.3))
        sprites, shapes = [], []
        for nd in nodes:
            sp, shp = self._node_sprites(nd, v, sz, max_w, False)
            sprites.append(sp)
            shapes.append(shp)
        nw = max(sp.w for sp in sprites)
        nh = max(sp.h for sp in sprites)
        layout = _NODE_LAYOUT.get(n, _NODE_LAYOUT[6])
        centers: dict[str, tuple[float, float]] = {}
        for k, nd in enumerate(nodes):
            fx, fy = nd.get("at") or layout[k]
            cx = area.x + nw / 2 + fx * max(0, area.w - nw)
            cy = area.y + nh / 2 + fy * max(0, area.h - nh)
            centers[nd["id"]] = (cx, cy)
        # 노드
        for k, nd in enumerate(nodes):
            sp = sprites[k]
            cx, cy = centers[nd["id"]]
            anchor = sp.meta["anchor"]
            x, y = cx - sp.w / 2, cy - anchor
            y = max(area.y, min(area.b - sp.h, y))
            centers[nd["id"]] = (cx, y + anchor)
            keys = {}
            if nd["id"] in hl:
                hsp, _ = self._node_sprites(nd, v, sz, max_w, True)
                keys[self.s_c] = Key(sprite=hsp, dur=0.4, delay=0.15)
            self.add(sp, x, y, show=0, enter="pop", delay=0.1 + 0.15 * k, dur=0.45, z=3, keys=keys)
        # 화살표
        thick = max(5, int(u * 0.24))
        color = pal.ink2 if v == 0 else pal.primary
        pair_count: dict[frozenset, int] = {}
        for e in edges:
            key = frozenset((str(e["from"]), str(e["to"])))
            pair_count[key] = pair_count.get(key, 0) + 1
        seen: dict[frozenset, int] = {}
        group_k: dict[int, int] = {}
        id_index = {nd["id"]: k for k, nd in enumerate(nodes)}
        for j, e in enumerate(edges):
            a, b = str(e["from"]), str(e["to"])
            key = frozenset((a, b))
            idx = seen.get(key, 0)
            seen[key] = idx + 1
            p0, p1 = centers[a], centers[b]
            L = max(1e-6, math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
            dx, dy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
            px = py = 0.0
            if pair_count[key] > 1:            # 같은 두 노드 사이 여러 화살표: 정렬한 id 방향 기준으로 나란히 벌린다
                ca, cb = sorted((a, b))
                q0, q1 = centers[ca], centers[cb]
                cl = max(1e-6, math.hypot(q1[0] - q0[0], q1[1] - q0[1]))
                off = (idx - (pair_count[key] - 1) / 2) * u * 2.2
                px, py = -(q1[1] - q0[1]) / cl * off, (q1[0] - q0[0]) / cl * off
            ta = _exit(shapes[id_index[a]], dx, dy)
            tb_ = _exit(shapes[id_index[b]], -dx, -dy)
            s0 = (p0[0] + px + dx * ta, p0[1] + py + dy * ta)
            s1 = (p1[0] + px - dx * tb_, p1[1] + py - dy * tb_)
            if math.hypot(s1[0] - s0[0], s1[1] - s0[1]) < thick * 6:
                self.warn(f"관계 화살표 {a}→{b} 가 너무 짧습니다 — 노드 at 위치를 벌리세요.")
                continue
            sp, bx, by = arrow_sprite(s0, s1, color, thick, dashed=bool(e.get("dashed")))
            st = self.edge_steps[j]
            order = group_k.get(st, 0)
            group_k[st] = order + 1
            d0 = (0.75 if st == 0 else 0.05) + 0.45 * order
            enter = "wipe" if (dx > 0 and abs(dx) >= abs(dy)) else ("wipe_down" if (dy > 0 and abs(dy) > abs(dx)) else "fade")
            self.add(sp, bx, by, show=st, enter=enter, delay=d0, dur=0.5, z=1)
            lab = str(e.get("label") or "")
            if lab:
                lp = pill(self, lab, size=int(S.small * 0.92), fill=pal.bg, color=pal.ink, outline=color, width=3, role="bold",
                          max_w=int(area.w * 0.5))
                mx, my = (s0[0] + s1[0]) / 2, (s0[1] + s1[1]) / 2
                lx = max(area.x, min(area.r - lp.width, mx - lp.width / 2))
                ly = max(area.y, min(area.b - lp.height, my - lp.height / 2))
                self.add(D.plain(lp), lx, ly, show=st, enter="pop", delay=d0 + 0.4, dur=0.4, z=2)


def _exit(shape: tuple[str, float, float], dx: float, dy: float) -> float:
    """중심에서 방향 (dx, dy) 로 나갈 때 모양 경계까지 거리."""
    kind, a, b = shape
    if kind == "circle":
        return a
    tx = a / abs(dx) if abs(dx) > 1e-6 else math.inf
    ty = b / abs(dy) if abs(dy) > 1e-6 else math.inf
    return min(tx, ty)


# ================================================================ calc

@scene("calc")
class Calc(SceneRenderer):
    """계산 단계: 조건 카드 → 수식 단계 전개(문장마다 한 줄, 앞 줄은 흐리게) → 정답 vs 흔한 오답 막대(+근거 배지).

    params: conditions[글...], steps[수식 글...], answer(숫자), unit?, answer_text?(정답 표시 글),
            distractors?[{label, value, text?}](흔한 오답), answer_label?(기본 '정답'), basis?
    distractors 가 없으면 막대 대신 큰 정답 카드. 세로에서 셋이 다 안 들어가면 막대 단계에 조건 카드를 접고 수식을 위로 올린다.
    변형: 0 조건 알약 + 번호 원 / 1 조건 카드 + '=' 줄 / 2 진한 조건 띠 + 계단식 수식
    """

    required = ("conditions", "steps", "answer")
    basis_auto = False

    def validate(self) -> None:
        super().validate()
        sid = self.scene.id
        for k in ("conditions", "steps"):
            val = self.p.get(k)
            if not isinstance(val, list) or not val or not all(isinstance(x, (str, int, float)) for x in val):
                raise SceneError(f"장면 {sid}(calc): {k} 는 글 목록이어야 합니다.")
        if len(self.p["steps"]) > 6:
            raise SceneError(f"장면 {sid}(calc): steps 는 6줄 이하로 쓰세요.")
        self.answer_v = _num(self.p.get("answer"))
        if self.answer_v is None:
            raise SceneError(f"장면 {sid}(calc): answer 는 숫자여야 합니다(예: 18000000).")
        ds = self.p.get("distractors") or []
        if not isinstance(ds, list) or any(not isinstance(d, dict) or "label" not in d or _num(d.get("value")) is None for d in ds):
            raise SceneError(f"장면 {sid}(calc): distractors 는 [{{label, value(숫자)}}, ...] 이어야 합니다.")
        if len(ds) > 3:
            raise SceneError(f"장면 {sid}(calc): distractors 는 3개 이하로 쓰세요.")

    def _phases(self) -> None:
        K, N = len(self.p["steps"]), self.n
        if N >= K + 2:
            self.step_steps = [1 + k for k in range(K)]
            bars = K + 1
        else:
            avail = max(1, N - 2)
            self.step_steps = [1 + s for s in self.spread(K, avail)]
            bars = 1 + avail
        self.s_bars = bars
        self.T = bars + 1

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        walk(beats, list(range(self.n)), list(range(self.T)), lambda st: 2.6 if st == self.s_bars else STEP_HOLD)
        if self.scene.hold_seconds > 0:
            beats.append(Beat("hold", self.T - 1, seconds=float(self.scene.hold_seconds)))
        return beats

    def build(self) -> None:
        self._phases()
        S, pal, u = self.g.s, self.pal, self.g.unit
        v = self.variant
        unit = str(self.p.get("unit") or "")
        C = self.content()
        box = header(self, C)
        conds = [str(c) for c in self.p["conditions"]]
        steps = [str(s) for s in self.p["steps"]]
        portrait = self.g.portrait
        if portrait:
            left = right = box
        else:
            left, right = box.split_x(0.56, int(u * 3))
        gap = int(u * 1.2)
        cond_sp = self._cond(conds, left.w, left.h * (0.34 if portrait else 0.42), v)
        bars_items = self._bars_data(unit)
        bars_h_est = self._bars_height(bars_items, right.w)
        collapse = False
        if portrait:
            # 셋(조건·수식·막대)이 다 들어가면 그대로, 아니면 막대 단계에 조건을 접고 수식을 위로 올린다
            rows, steps_h, ok = self._steps(steps, left.w, box.h - cond_sp.h - bars_h_est - 2 * gap, v, quiet=True)
            if not ok:
                collapse = True
                rows, steps_h, _ = self._steps(steps, left.w, min(box.h - cond_sp.h - gap, box.h - bars_h_est - gap), v)
        else:
            rows, steps_h, _ = self._steps(steps, left.w, left.h - cond_sp.h - gap, v)
        if portrait:
            total = cond_sp.h + gap + steps_h + (gap + bars_h_est if not collapse else 0)
            y = vcenter(total, box, 0.4)
        else:
            y = vcenter(cond_sp.h + gap + steps_h, left, 0.4)
        cond_y = y
        steps_y = y + cond_sp.h + gap
        ckeys = {self.s_bars: Key(alpha=0.0, dur=0.35)} if collapse else {}
        self.add(cond_sp, left.x, cond_y, show=0, enter="rise", z=1, keys=ckeys)
        ry = steps_y
        shift = (steps_y - box.y) if collapse else 0.0
        for k, (sp, st) in enumerate(zip(rows, self.step_steps)):
            keys: dict[int, Key] = {}
            nxt = next((s for s in self.step_steps[k + 1:] if s > st), None)
            if nxt is not None:
                keys[nxt] = Key(alpha=0.5, dur=0.35)
            if collapse:
                prev = keys.get(self.s_bars)
                keys[self.s_bars] = Key(y=ry - shift, alpha=prev.alpha if prev else None, delay=0.1, dur=0.55)
            order = self.step_steps[:k].count(st)
            self.add(sp, left.x, ry, show=st, enter="left", delay=0.05 + 0.35 * order, z=2, keys=keys)
            ry += sp.h + self._step_gap
        if portrait:
            by = (ry - shift + gap) if collapse else (ry + gap)
            barea = Box(box.x, int(by), box.w, int(box.b - by))
        else:
            barea = right
        self._place_bars(bars_items, barea, unit)
        place_basis(self, show=self.s_bars, delay=0.8)

    def _cond(self, conds: list[str], w: int, max_h: float, v: int) -> Sprite:
        S, pal, u = self.g.s, self.pal, self.g.unit
        sz = S.body
        lab = pill(self, "조건", size=int(S.small * 0.88), fill=pal.primary if v != 2 else pal.accent, color=pal.on_primary if v != 2 else pal.ink)
        pad = int(u * 1.0)
        dark = v == 2
        while True:
            tbs = [D.text_block(self.fonts, f"· {c}" if v != 0 else c, size=sz, max_w=w - 2 * pad - (int(sz * 0.9) if v == 0 else 0),
                                color=pal.on_primary if dark else pal.ink, emph=pal.accent if dark else pal.primary, max_lines=2) for c in conds]
            inner = sum(t.h for t in tbs) + int(sz * 0.2) * (len(tbs) - 1)
            H = int(lab.height + u * 0.6 + inner + 2 * pad)
            if H <= max_h or sz <= S.small:
                break
            sz -= 2
        if H > max_h:
            self.warn("조건이 많거나 길어 카드가 큽니다 — 조건을 줄이세요.")
        fill = pal.primary if dark else (pal.surface if v == 0 else pal.primary_soft)
        im = D.rounded(w, H, u, fill, outline=pal.line if v == 0 else None, width=2 if v == 0 else 0)
        D.paste(im, lab, pad, pad)
        y = pad + lab.height + u * 0.6
        for t in tbs:
            if v == 0:
                D.paste(im, D.ellipse(int(sz * 0.32), int(sz * 0.32), pal.accent), pad + sz * 0.1, y + t.line_h / 2 - sz * 0.16)
                D.paste(im, t.img, pad + int(sz * 0.9) - t.ox, y - t.oy)
            else:
                D.paste(im, t.img, pad - t.ox, y - t.oy)
            y += t.h + int(sz * 0.2)
        return D.shadowed(im, blur=12, offset=(0, 5), alpha=0.12 if v != 1 else 0.0)

    def _steps(self, steps: list[str], w: int, max_h: float, v: int, *, quiet: bool = False) -> tuple[list[Sprite], int, bool]:
        """수식 줄 그림들, 전체 높이, 들어갔는지. quiet=True 면 넘쳐도 경고하지 않는다(시험 삼아 재 볼 때)."""
        S, pal, u = self.g.s, self.pal, self.g.unit
        sz = S.lead
        gap = 0
        while True:
            nb = int(sz * 1.05)
            gap = int(sz * 0.35)
            indent = (lambda k: int(k * u * 1.2)) if v == 2 else (lambda k: 0)
            tbs = [D.text_block(self.fonts, s, role="bold", size=sz, max_w=w - nb - int(sz * 0.5) - indent(k) - 4, color=pal.ink,
                                emph=pal.primary, max_lines=2) for k, s in enumerate(steps)]
            hs = [max(nb, t.h) for t in tbs]
            total = sum(hs) + gap * (len(hs) - 1)
            fits = total <= max_h and not any(t.broke or t.overflow for t in tbs)
            if fits or sz <= S.body_min:
                break
            sz -= 2
        if not fits and not quiet:
            self.warn("수식 단계가 많거나 길어 화면을 넘칩니다 — 단계를 줄이거나 짧게 쓰세요.")
        out = []
        for k, (t, h) in enumerate(zip(tbs, hs)):
            ind = indent(k)
            W = w
            im = D.blank(W, h)
            if v == 1:
                mb = D.text_block(self.fonts, str(k + 1), role="heavy", size=int(nb * 0.56), color=pal.on_primary, max_w=nb, max_lines=1)
                circ = D.rounded(nb, nb, int(nb * 0.28), pal.primary)
                D.paste(circ, mb.img, (nb - mb.w) / 2 - mb.ox, (nb - mb.h) / 2 - mb.oy)
                D.paste(im, circ, ind, (h - nb) / 2)
            else:
                D.paste(im, badge(self, str(k + 1), nb, fill=pal.primary if v == 0 else pal.accent, color=pal.on_primary if v == 0 else pal.ink),
                        ind, (h - nb) / 2)
            D.paste(im, t.img, ind + nb + int(sz * 0.5) - t.ox, (h - t.h) / 2 - t.oy)
            out.append(D.plain(im))
        self._step_gap = gap
        return out, int(total), fits

    def _bars_data(self, unit: str) -> list[tuple[str, float, str, bool]]:
        label = str(self.p.get("answer_label") or "정답")
        ans_text = str(self.p.get("answer_text") or fmt_num(self.answer_v, unit))
        items = [(label, self.answer_v, ans_text, True)]
        for d in self.p.get("distractors") or []:
            val = _num(d["value"])
            items.append((str(d["label"]), val, str(d.get("text") or fmt_num(val, unit)), False))
        return items

    def _bars_height(self, items, w: int) -> int:
        S = self.g.s
        if len(items) == 1:
            return int(S.hero * 1.0 + S.small * 1.36 + self.g.unit * 2.4)
        row = S.small * 1.36 + S.body * 0.25 + S.body * 1.0
        return int(row * len(items) + S.body * 0.5 * (len(items) - 1))

    def _place_bars(self, items, area: Box, unit: str) -> None:
        S, pal, u = self.g.s, self.pal, self.g.unit
        st = self.s_bars
        if len(items) == 1:
            label, val, text, _ = items[0]
            lab = label_pill(self, label, fill=pal.correct, color=D.WHITE, size=S.body)
            tb = self.text(text, role="heavy", size=S.hero, min_size=S.h2, max_w=area.w - u * 2, max_lines=1, color=pal.correct)
            pad = int(u * 1.2)
            W = int(max(lab.w + pad * 2, tb.w + pad * 2))
            H = int(lab.h + u * 0.6 + tb.h + 2 * pad)
            im = D.rounded(W, H, u, D.mix(pal.correct, D.WHITE, 0.88), outline=pal.correct, width=4)
            D.paste(im, lab.img, (W - lab.w) / 2, pad)
            D.paste(im, tb.img, (W - tb.w) / 2 - tb.ox, pad + lab.h + u * 0.6 - tb.oy)
            sp = D.shadowed(im, blur=14, offset=(0, 6), alpha=0.16)
            self.add(sp, area.x + (area.w - W) / 2 if not self.g.portrait else area.x, vcenter(H, area, 0.3), show=st, enter="pop", delay=0.2,
                     dur=0.5, z=3)
            return
        vmax = max(max(abs(v) for _, v, _, _ in items), 1e-9)
        lsz, vsz = S.small, S.body
        val_w = int(max(self.fonts.get("heavy", vsz).getlength(t) for _, _, t, _ in items) + u)
        bar_h = int(vsz * 1.0)
        rows = []
        for label, val, text, ok in items:
            lb = D.text_block(self.fonts, label, role="bold", size=lsz, max_w=area.w, max_lines=1, color=pal.correct if ok else pal.ink2)
            rows.append((lb, val, text, ok))
        row_h = [int(lb.h + vsz * 0.25 + bar_h) for lb, _, _, _ in rows]
        gapr = int(vsz * 0.5)
        total = sum(row_h) + gapr * (len(rows) - 1)
        y = vcenter(total, area, 0.35)
        bar_area = area.w - val_w - int(u * 0.6)
        for k, ((lb, val, text, ok), rh) in enumerate(zip(rows, row_h)):
            d0 = 0.15 + 0.3 * k
            self.add(lb, area.x, y, show=st, enter="fade", delay=d0, z=2)
            by = y + lb.h + vsz * 0.25
            bw = max(int(bar_h * 0.8), int(bar_area * abs(val) / vmax))
            col = pal.correct if ok else D.mix(pal.muted, pal.bg, 0.1)
            self.add(D.plain(D.rounded(bw, bar_h, bar_h // 3, col)), area.x, by, show=st, enter="wipe", delay=d0 + 0.1, dur=0.75, z=2)
            vt = D.text_block(self.fonts, text, role="heavy", size=vsz, max_w=val_w * 2, max_lines=1, color=pal.correct if ok else pal.ink2)
            self.add(vt, area.x + bw + u * 0.5, by + (bar_h - vt.h) / 2, show=st, enter="fade", delay=d0 + 0.8, z=2)
            if ok:
                ic = int(bar_h * 0.7)
                if bw > ic * 1.8:
                    self.add(D.plain(D.icon_check(ic, D.WHITE, width=ic * 0.16)), area.x + bw - ic - bar_h * 0.2, by + (bar_h - ic) / 2,
                             show=st, enter="pop", delay=d0 + 0.85, z=3)
            y += rh + gapr


# ================================================================ diff

@scene("diff")
class Diff(SceneRenderer):
    """개정 전후 비교: 구법 → 신법 → 바뀐 부분만 색 강조 → 시행일 타임라인(+다음 시험일 표시).

    params: before{label?(기본 '개정 전'), text}, after{label?(기본 '개정 후'), text}, changed?[바뀐 글 조각...],
            effective_date(신법 시행일), title?, exam_date?(기본 channel.yaml exam.next_exam_date), show_exam?(기본 true), basis?
    가로는 좌우, 세로는 위(구법)·아래(신법). 구법의 바뀐 조각은 회색, 신법의 바뀐 조각은 강조색 형광펜.
    변형: 0 흰 카드 두 장 + 화살표 / 1 색 머리띠 카드 / 2 구법 흐린 카드 + 신법 진한 테두리
    """

    required = ("before", "after")
    basis_auto = False

    def validate(self) -> None:
        super().validate()
        sid = self.scene.id
        for k in ("before", "after"):
            val = self.p.get(k)
            if not isinstance(val, dict) or not str(val.get("text") or "").strip():
                raise SceneError(f"장면 {sid}(diff): {k} 는 {{label, text}} 이고 text 가 있어야 합니다.")
        ch = self.p.get("changed") or []
        if not isinstance(ch, list) or not all(isinstance(c, str) and c.strip() for c in ch):
            raise SceneError(f"장면 {sid}(diff): changed 는 바뀐 글 조각 목록이어야 합니다.")
        self.eff = parse_date(self.p.get("effective_date"))
        if self.p.get("effective_date") is not None and self.eff is None:
            raise SceneError(f"장면 {sid}(diff): effective_date 는 YYYY-MM-DD 날짜여야 합니다.")
        if self.eff is None:
            self.warn("effective_date(시행일)가 없어 타임라인을 뺐습니다 — 개정 비교에는 시행일을 꼭 쓰세요.")

    def _phases(self) -> None:
        units = ["before", "after"]
        self.changed = [c.strip() for c in (self.p.get("changed") or [])]
        if self.changed:
            units.append("hl")
        if self.eff:
            units.append("tl")
        groups = merge_groups(units, self.n, ["after", "tl", "hl"])
        self.step_of = {name: k for k, g in enumerate(groups) for name in g}
        self.T = len(groups)

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        walk(beats, list(range(self.n)), list(range(self.T)), lambda st: 2.4 if st == self.T - 1 else STEP_HOLD)
        if self.scene.hold_seconds > 0:
            beats.append(Beat("hold", self.T - 1, seconds=float(self.scene.hold_seconds)))
        return beats

    def _marked(self, text: str) -> tuple[str, int]:
        """바뀐 조각을 **강조** 로 감싼 글과 찾은 개수."""
        plain_ = D.strip_emphasis(text)
        spans: list[tuple[int, int]] = []
        for c in sorted(self.changed, key=len, reverse=True):
            start = 0
            while True:
                i = plain_.find(c, start)
                if i < 0:
                    break
                if all(i >= e or i + len(c) <= s for s, e in spans):
                    spans.append((i, i + len(c)))
                start = i + len(c)
        spans.sort()
        out, pos = [], 0
        for s, e in spans:
            out.append(plain_[pos:s])
            out.append(f"**{plain_[s:e]}**")
            pos = e
        out.append(plain_[pos:])
        return "".join(out), len(spans)

    def build(self) -> None:
        self._phases()
        S, pal, u = self.g.s, self.pal, self.g.unit
        v = self.variant
        C = self.content()
        box = header(self, C)
        title = str(self.p.get("title") or "")
        if title:
            tt = self.text(title, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2, emph=pal.primary)
            self.add(tt, box.x, box.y, show=0, enter="left")
            _, box = box.take_top(tt.h, int(tt.size * 0.7))
        bf, af = self.p["before"], self.p["after"]
        b_text, a_text = str(bf["text"]), str(af["text"])
        b_mark, nb = self._marked(b_text)
        a_mark, na = self._marked(a_text)
        if self.changed and nb + na == 0:
            self.warn("changed 의 글 조각이 before·after 어디에도 없어 강조를 못 했습니다.")
        tl_h = int(S.small * 1.4 * 2 + u * 2.2) if self.eff else 0
        area = Box(box.x, box.y, box.w, int(box.h - (tl_h + u * 1.2 if tl_h else 0)))
        arrow = int(u * 2.6)
        if self.g.portrait:
            cw, ch_max = area.w, (area.h - arrow) / 2
        else:
            cw, ch_max = (area.w - arrow) // 2, area.h
        b_lab = str(bf.get("label") or "개정 전")
        a_lab = str(af.get("label") or "개정 후")
        sz = S.lead
        while True:
            b0 = self._card(b_lab, b_text, cw, sz, old=True, marked=None)
            a0 = self._card(a_lab, a_text, cw, sz, old=False, marked=None)
            if (max(b0.h, a0.h) <= ch_max and not b0.meta["broke"] and not a0.meta["broke"]) or sz <= S.body_min:
                break
            sz -= 2
        if max(b0.h, a0.h) > ch_max:
            self.warn("조문이 길어 화면을 넘칩니다 — 바뀐 부분 앞뒤만 짧게 인용하세요.")
        b1 = self._card(b_lab, b_mark, cw, sz, old=True, marked=True) if self.changed else b0
        a1 = self._card(a_lab, a_mark, cw, sz, old=False, marked=True) if self.changed else a0
        s_b, s_a = self.step_of["before"], self.step_of["after"]
        s_hl = self.step_of.get("hl")
        if self.g.portrait:
            ch = max(b0.h, a0.h)
            total = b0.h + arrow + a0.h
            y = vcenter(total, area, 0.4)
            bx, by = area.x, y
            ax, ay = area.x, y + b0.h + arrow
            ar = D.icon_arrow(int(arrow - u * 0.6), max(6, u // 4), pal.accent, "down")
            arx, ary = area.cx - ar.width / 2, by + b0.h + (arrow - ar.height) / 2
            enter_ar = "wipe_down"
        else:
            ch = max(b0.h, a0.h)
            y = vcenter(ch, area, 0.4)
            bx, by = area.x, y
            ax, ay = area.x + cw + arrow, y
            ar = D.icon_arrow(int(arrow - u * 0.6), max(6, u // 4), pal.accent, "right")
            arx, ary = area.x + cw + (arrow - ar.width) / 2, y + ch / 2 - ar.height / 2
            enter_ar = "wipe"
        bkeys = {s_hl: Key(sprite=b1, dur=0.45)} if s_hl is not None else {}
        akeys = {s_hl: Key(sprite=a1, dur=0.45, delay=0.2)} if s_hl is not None else {}
        self.add(b0, bx, by, show=s_b, enter="rise", z=1, keys=bkeys)
        self.add(D.plain(ar), arx, ary, show=s_a, enter=enter_ar, dur=0.4, z=1)
        self.add(a0, ax, ay, show=s_a, enter="rise" if self.g.portrait else "left", delay=0.25 if s_a == s_b else 0.1, z=1, keys=akeys)
        if self.eff:
            self._timeline(Box(box.x, int(box.b - tl_h), box.w, tl_h), self.step_of["tl"])
        place_basis(self, show=0, delay=0.6)

    def _card(self, label: str, text: str, w: int, sz: int, *, old: bool, marked: bool | None) -> Sprite:
        S, pal, u = self.g.s, self.pal, self.g.unit
        v = self.variant
        pad = int(sz * 0.55)
        if marked:
            emph, marker = (pal.ink2, D.mix(pal.line, pal.bg, 0.2)) if old else (pal.primary, pal.accent_soft)
        else:
            emph, marker = None, None
        ink = pal.ink2 if (old and v == 2) else pal.ink
        tb = D.text_block(self.fonts, text, size=sz, max_w=w - 2 * pad, color=ink, emph=emph, marker=marker, lh=1.42)
        lab_fill = (D.mix(pal.muted, pal.ink, 0.35) if old else pal.primary)
        if v == 1:
            band_h = int(S.small * 1.7)
            lb = D.text_block(self.fonts, label, role="heavy", size=S.small, color=D.WHITE, max_w=w - 2 * pad, max_lines=1)
            H = int(band_h + pad + tb.h + pad)
            im = D.rounded(w, H, u, pal.surface, outline=pal.line, width=2)
            D.paste(im, _top_rounded(w, band_h, u, lab_fill), 0, 0)
            D.paste(im, lb.img, pad - lb.ox, (band_h - lb.h) / 2 - lb.oy)
            D.paste(im, tb.img, pad - tb.ox, band_h + pad - tb.oy)
        else:
            lp = pill(self, label, size=int(S.small * 0.9), fill=lab_fill, color=D.WHITE)
            ov = lp.height // 2
            H = int(ov + pad * 0.6 + ov + tb.h + pad)
            im = D.blank(w, H)
            if v == 2:
                fill = D.mix(pal.surface, pal.bg, 0.6) if old else pal.surface
                outline, bw = (pal.line, 2) if old else (pal.primary, 5)
            else:
                fill, outline, bw = pal.surface, pal.line, 2
            D.paste(im, D.rounded(w, H - ov, u, fill, outline=outline, width=bw), 0, ov)
            D.paste(im, lp, pad, 0)
            D.paste(im, tb.img, pad - tb.ox, ov + ov + pad * 0.6 - tb.oy)
        sp = D.shadowed(im, blur=12, offset=(0, 5), alpha=0.06 if old else 0.14)
        sp.meta["broke"] = tb.broke
        return sp

    def _timeline(self, b: Box, st: int) -> None:
        S, pal, u = self.g.s, self.pal, self.g.unit
        info = exam_info(self.ctx.cfg)
        show_exam = self.p.get("show_exam", True) is not False
        exam_d = parse_date(self.p.get("exam_date")) or info["next_exam_date"]
        marks = [(self.eff, f"{dot_date(self.eff)} 시행", pal.accent, True)]
        if show_exam and exam_d:
            rnd = info.get("round")
            marks.append((exam_d, f"{'제' + str(rnd) + '회 ' if rnd else ''}시험 {dot_date(exam_d)}", pal.primary, False))
        lo = min(m[0] for m in marks)
        hi = max(m[0] for m in marks)
        x0, x1 = b.x + b.w * 0.16, b.r - b.w * 0.16
        line_y = b.y + b.h / 2
        th = max(8, u // 3)
        self.add(D.plain(D.rounded(b.w, th, th // 2, pal.line)), b.x, line_y - th / 2, show=st, enter="wipe", dur=0.55, z=1)

        def xof(d: date) -> float:
            if hi == lo:
                return (x0 + x1) / 2
            return x0 + (x1 - x0) * (d - lo).days / (hi - lo).days

        ex = xof(self.eff)
        self.add(D.plain(D.rounded(int(b.r - ex), th, th // 2, pal.accent)), ex, line_y - th / 2, show=st, enter="wipe", delay=0.45, dur=0.5, z=1)
        for k, (d, text, col, above) in enumerate(marks):
            x = xof(d)
            dot = int(u * 1.3)
            self.add(D.plain(D.ellipse(dot, dot, D.WHITE, outline=col, width=max(5, dot // 4))), x - dot / 2, line_y - dot / 2, show=st,
                     enter="pop", delay=0.6 + 0.3 * k, z=3)
            lp = pill(self, text, size=int(S.small * 0.92), fill=col if above else pal.surface, color=D.WHITE if above else col,
                      outline=None if above else col, width=0 if above else 3)
            lx = max(b.x, min(b.r - lp.width, x - lp.width / 2))
            ly = line_y - dot / 2 - u * 0.4 - lp.height if above else line_y + dot / 2 + u * 0.4
            self.add(D.plain(lp), lx, ly, show=st, enter="rise" if above else "fade", delay=0.75 + 0.3 * k, z=3)


# ================================================================ ranking

@scene("ranking")
class Ranking(SceneRenderer):
    """오답 랭킹(앱 데이터): 순위 막대가 낮은 순위부터 하나씩 → focus 항목 확대 해설(다른 막대는 흐려짐).

    params: items[{label, value, note?}](1위부터, 2~7개), unit?(기본 '%'), focus_index?(0부터, 기본 0=1위), title?,
            note?(집계 출처·기간 작은 글, 예: '함정노트 앱 · 10.1~10.7 · 풀이 3,214회'), explanation?(확대 카드 해설), basis?
    변형: 0 가로 막대 / 1 막대 안 값 + 1위 왕관 색 / 2 카드 줄(순위 원 + 막대)
    """

    required = ("items",)
    basis_auto = False

    def validate(self) -> None:
        super().validate()
        sid = self.scene.id
        items = self.p.get("items")
        if not isinstance(items, list) or not 2 <= len(items) <= 7 or any(
                not isinstance(i, dict) or "label" not in i or _num(i.get("value")) is None for i in items):
            raise SceneError(f"장면 {sid}(ranking): items 는 [{{label, value(숫자)}}, ...] 2~7개여야 합니다(1위부터).")
        f = self.p.get("focus_index", 0)
        if not isinstance(f, int) or isinstance(f, bool) or not 0 <= f < len(items):
            raise SceneError(f"장면 {sid}(ranking): focus_index 는 0부터 {len(items) - 1} 사이 정수여야 합니다.")
        self.focus = f
        if not str(self.p.get("note") or "").strip():
            self.warn("note(집계 출처·기간)가 없습니다 — 앱 데이터 랭킹에는 집계 기간과 표본을 적으세요.")

    def _phases(self) -> None:
        M, N = len(self.p["items"]), self.n
        b_count = max(1, min(M, N - 1))
        order = list(range(M))[::-1]                 # 낮은 순위부터 공개
        steps = self.spread(M, b_count)
        self.bar_step = {item: steps[k] for k, item in enumerate(order)}
        self.s_focus = b_count
        self.T = b_count + 1

    def plan(self) -> list[Beat]:
        beats = [Beat("lead", -1)]
        it = self.p["items"][self.focus]
        walk(beats, list(range(self.n)), list(range(self.T)),
             lambda st: read_seconds(f"{it['label']} {it.get('note') or self.p.get('explanation') or ''}") if st == self.s_focus else STEP_HOLD)
        if self.scene.hold_seconds > 0:
            beats.append(Beat("hold", self.T - 1, seconds=float(self.scene.hold_seconds)))
        return beats

    def build(self) -> None:
        self._phases()
        S, pal, u = self.g.s, self.pal, self.g.unit
        v = self.variant
        unit = str(self.p.get("unit") if self.p.get("unit") is not None else "%")
        items = [(str(i["label"]), _num(i["value"]), str(i.get("note") or "")) for i in self.p["items"]]
        M = len(items)
        C = self.content()
        box = header(self, C)
        title = str(self.p.get("title") or "")
        note = str(self.p.get("note") or "")
        y = box.y
        if title:
            tt = self.text(title, role="heavy", size=S.h2, min_size=S.body_min, max_w=box.w, max_lines=2, emph=pal.primary)
            self.add(tt, box.x, y, show=0, enter="left")
            y += tt.h + u * 0.4
        if note:
            nt = self.text(note, role="bold", size=S.small, max_w=box.w, max_lines=2, color=pal.ink2)
            self.add(nt, box.x, y, show=0, enter="fade", delay=0.2)
            y += nt.h
        if title or note:
            y += u * 1.1
        area = Box(box.x, int(y), box.w, int(box.b - y))
        vmax = max(max(val for _, val, _ in items), 1e-9)
        stacked = self.g.portrait
        sz = S.body
        while True:
            rb = int(sz * 1.25)
            lab_w = area.w - rb - int(sz * 0.5) if stacked else int(area.w * 0.42)
            labs = [D.text_block(self.fonts, lab, role="bold", size=sz, max_w=lab_w, max_lines=2, color=pal.ink, emph=pal.primary) for lab, _, _ in items]
            bar_h = int(sz * (0.8 if stacked else 1.0))
            rows_h = [int(max(rb, lb.h + sz * 0.2 + bar_h)) if stacked else int(max(rb, lb.h, bar_h)) for lb in labs]
            gap = int(sz * (0.45 if v != 2 else 0.3))
            pads = int(sz * 0.35) if v == 2 else 0
            total = sum(h + 2 * pads for h in rows_h) + gap * (M - 1)
            if (total <= area.h and not any(lb.broke for lb in labs)) or sz <= S.small:
                break
            sz -= 2
        if total > area.h:
            self.warn("항목이 많거나 길어 화면을 넘칩니다 — 항목을 5개 안팎으로 줄이세요.")
        yy = vcenter(total, area, 0.38)
        vsz = int(sz * 1.05)
        val_w = int(max(self.fonts.get("heavy", vsz).getlength(fmt_num(val, unit)) for _, val, _ in items) + u)
        row_y: list[float] = []
        for k, ((lab, val, _), lb, rh) in enumerate(zip(items, labs, rows_h)):
            st = self.bar_step[k]
            dim = {self.s_focus: Key(alpha=0.14, dur=0.4)}
            top = k == 0
            col = pal.primary if top else D.mix(pal.primary, pal.bg, 0.45)
            ry = yy + pads
            if v == 2:
                self.add(card(area.w, rh + 2 * pads, fill=pal.surface, radius=int(u * 0.6), border=pal.line, border_w=2, shadow_alpha=0.06, blur=8),
                         area.x, yy, show=st, enter="left", z=0, keys=dict(dim))
            rx = area.x + pads
            rbadge = badge(self, str(k + 1), rb, fill=pal.accent if top else pal.primary_soft, color=pal.ink if top else pal.primary)
            self.add(D.plain(rbadge), rx, ry + (rh - rb) / 2 if not stacked else ry, show=st, enter="pop", delay=0.05, z=2, keys=dict(dim))
            if stacked:
                lx = rx + rb + int(sz * 0.5)
                self.add(lb, lx, ry, show=st, enter="fade", delay=0.1, z=2, keys=dict(dim))
                by = ry + lb.h + sz * 0.2
                bar_x = lx
                bar_area = area.r - pads - bar_x - val_w
            else:
                lx = rx + rb + int(sz * 0.5)
                self.add(lb, lx, ry + (rh - lb.h) / 2, show=st, enter="fade", delay=0.1, z=2, keys=dict(dim))
                bar_x = area.x + int(area.w * 0.42) + rb + int(sz * 0.9)
                by = ry + (rh - bar_h) / 2
                bar_area = area.r - pads - bar_x - val_w
            bw = max(int(bar_h * 0.8), int(bar_area * max(0.0, val) / vmax))
            self.add(D.plain(D.rounded(bw, bar_h, bar_h // 3, col)), bar_x, by, show=st, enter="wipe", delay=0.2, dur=0.7, z=2, keys=dict(dim))
            text = fmt_num(val, unit)
            if v == 1 and top and bw > val_w + sz:
                vt = D.text_block(self.fonts, text, role="heavy", size=vsz, max_w=val_w * 2, max_lines=1, color=pal.on_primary)
                vx = bar_x + bw - vt.w - sz * 0.35
            else:
                vt = D.text_block(self.fonts, text, role="heavy", size=vsz, max_w=val_w * 2, max_lines=1, color=pal.primary if top else pal.ink2)
                vx = bar_x + bw + u * 0.5
            self.add(vt, vx, by + (bar_h - vt.h) / 2, show=st, enter="fade", delay=0.8, z=3, keys=dict(dim))
            row_y.append(yy)
            yy += rh + 2 * pads + gap
        self._focus_card(area, items, unit)
        place_basis(self, show=0, delay=0.6)

    def _focus_card(self, area: Box, items, unit: str) -> None:
        S, pal, u = self.g.s, self.pal, self.g.unit
        k = self.focus
        lab, val, inote = items[k]
        exp = inote or str(self.p.get("explanation") or "")
        w = area.w if self.g.portrait else int(area.w * 0.8)
        pad = int(u * 1.4)
        head = pill(self, f"{k + 1}위 · {fmt_num(val, unit)}", size=S.body, fill=pal.primary, color=pal.on_primary, role="heavy")
        lt = self.text(lab, role="heavy", size=S.h2, min_size=S.body_min, max_w=w - 2 * pad, max_h=area.h * 0.45, lh=1.3, emph=pal.primary,
                       balance=True)
        et = self.text(exp, size=S.body, min_size=S.body_min, max_w=w - 2 * pad, max_h=area.h * 0.4, color=pal.ink2, emph=pal.primary) if exp else None
        H = int(pad + head.height + u * 0.9 + lt.h + ((u * 0.8 + et.h) if et else 0) + pad)
        im = D.rounded(w, H, u, pal.surface, outline=pal.primary, width=5)
        D.paste(im, head, pad, pad)
        yy = pad + head.height + u * 0.9
        D.paste(im, lt.img, pad - lt.ox, yy - lt.oy)
        if et:
            yy += lt.h + u * 0.8
            D.paste(im, D.rounded(w - 2 * pad, 2, 1, pal.line), pad, yy - u * 0.4)
            D.paste(im, et.img, pad - et.ox, yy - et.oy)
        sp = D.shadowed(im, blur=20, offset=(0, 10), alpha=0.22)
        self.add(sp, area.x + (area.w - w) / 2, vcenter(min(H, area.h), area, 0.4), show=self.s_focus, enter="pop", delay=0.3, dur=0.5, z=8)
