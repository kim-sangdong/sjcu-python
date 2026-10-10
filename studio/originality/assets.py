"""원고(VideoScript·CardNews·BlogPost)를 검사용 문서(Doc)로 바꾼다.

문장은 '어느 장면의 몇 번째 문장인지'를 함께 들고 다닌다 — 고칠 지시(fixes)에 위치를 적기 위해서다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from studio import paths
from studio.models import BlogPost, CardNews, VideoScript, WeekPlan, read_model
from studio.originality.text import split_sentences

KIND_DIRS = {"longform": "longform", "short": "shorts", "cardnews": "cardnews", "blog": "blog"}
KIND_MODEL = {"longform": VideoScript, "short": VideoScript, "cardnews": CardNews, "blog": BlogPost}
DIAGRAM_LANGS = {"flow", "diagram", "도식", "흐름", "mermaid"}
_FAR = datetime.max.replace(tzinfo=timezone.utc)
_DECLARED = re.compile(r"형식\s*[:：]\s*([^\n]+)")


@dataclass
class Line:
    text: str
    scene_id: str      # 영상: 장면 id / 카드: slide01 / 블로그: 섹션 제목
    scene_kind: str    # 영상: 장면 kind / 카드: 슬라이드 kind / 블로그: p·list·table·quote·heading
    no: int            # 자산 안 문장 번호(1부터)


@dataclass
class Doc:
    asset_id: str
    kind: str
    week: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    skeleton: list[str] = field(default_factory=list)
    format: str | None = None                 # 영상 대표 형식(장면 kind 분포)
    declared_format: str = ""                 # notes 첫 줄 '형식: …' (참고용 — 판정은 장면 kind 분포로)
    format_mix: dict[str, float] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)   # 파생 관계(원본 롱폼 등)
    synthetic_media: bool | None = None
    voice_scenes: int = 0
    voice_files: list[Path] = field(default_factory=list)
    visual_blocks: int | None = None         # 블로그 표·도식 수(원고 기준)
    publish_at: datetime | None = None
    obj: Any = None

    @property
    def label(self) -> str:
        return short_label(self.asset_id, self.week)

    @property
    def order_key(self) -> tuple:
        t = self.publish_at
        if t is not None and t.tzinfo is None:
            t = t.replace(tzinfo=paths.KST)
        return (t or _FAR, self.asset_id)


def short_label(asset_id: str, week: str | None = None) -> str:
    """같은 주 자산은 'S04' 처럼 짧게, 다른 주는 전체 ID."""
    prefix = paths.compact(week) + "-" if week else None
    return asset_id[len(prefix):] if prefix and asset_id.startswith(prefix) else asset_id


# ---------------------------------------------------------------- 영상

FORMAT_TIE = 0.8   # 첫 본문 장면 kind 의 비중이 최댓값의 80% 이상이면 그것을 대표 형식으로(퀴즈 뒤 해설 목록 같은 경우)


def representative_format(script: VideoScript, frame_kinds: set[str], families: dict[str, str]) -> tuple[str, dict[str, float]]:
    """틀 장면(title·chapter·cta·outro)을 뺀 장면 kind 의 화면 시간 비중(내레이션 글자 수 + hold_seconds×5.5)에서 가장 큰 것.

    비중이 비슷하면(첫 본문 장면이 최댓값의 80% 이상) 첫 본문 장면의 kind — 시청자가 형식을 알아보는 건 첫 장면이다.
    """
    mix: dict[str, float] = {}
    first = None
    for s in script.scenes():
        if s.kind in frame_kinds:
            continue
        fam = families.get(s.kind, s.kind)
        first = first or fam
        chars = sum(len(re.sub(r"\s", "", x)) for x in s.narration)
        mix[fam] = mix.get(fam, 0.0) + max(chars, 1) + s.hold_seconds * 5.5
    if not mix:
        return "title-only", {}
    total = sum(mix.values())
    best = max(mix.items(), key=lambda kv: kv[1])[0]
    if first and mix[first] >= FORMAT_TIE * mix[best]:
        best = first
    return best, {k: round(v / total, 3) for k, v in mix.items()}


def video_doc(script: VideoScript, frame_kinds: set[str], families: dict[str, str]) -> Doc:
    lines: list[Line] = []
    voice_files: list[Path] = []
    voice_scenes = 0
    for sc in script.scenes():
        for raw in sc.narration:
            for s in split_sentences(raw):
                lines.append(Line(s, sc.id, sc.kind, len(lines) + 1))
        if sc.kind == "voice":
            voice_scenes += 1
            audio = sc.params.get("audio")
            if audio:
                p = Path(str(audio))
                voice_files.append(p if p.is_absolute() else paths.ROOT / p)
    fmt, mix = representative_format(script, frame_kinds, families)
    declared = _DECLARED.search(script.notes or "")
    return Doc(
        asset_id=script.id, kind=script.format, week=script.week, title=script.title, description=script.description,
        tags=list(script.tags), hashtags=list(script.hashtags), lines=lines, skeleton=[s.kind for s in script.scenes()],
        format=fmt, format_mix=mix, declared_format=declared.group(1).strip() if declared else "", synthetic_media=script.synthetic_media, voice_scenes=voice_scenes,
        voice_files=voice_files, obj=script,
    )


# ---------------------------------------------------------------- 카드뉴스

def card_doc(card: CardNews) -> Doc:
    lines: list[Line] = []
    for i, sl in enumerate(card.slides, start=1):
        for s in split_sentences(f"{sl.title}\n{sl.body}"):
            lines.append(Line(s, f"slide{i:02d}", sl.kind, len(lines) + 1))
    d = Doc(asset_id=card.id, kind="cardnews", week=card.week, title=card.title, description=card.caption,
            hashtags=list(card.hashtags), lines=lines, skeleton=[s.kind for s in card.slides],
            synthetic_media=card.synthetic_media, obj=card)
    if card.source_id:
        d.sources.add(card.source_id)
    return d


# ---------------------------------------------------------------- 블로그

_MD_LINK = re.compile(r"!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)")
_MD_INLINE = re.compile(r"`+|\*\*|__|~~|<[^>]+>")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_LIST = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _md_text(s: str) -> str:
    s = _MD_LINK.sub(lambda m: m.group(1) or "", s)
    return _MD_INLINE.sub("", s).strip()


def parse_blog(md: str) -> tuple[list[Line], list[str], int]:
    """마크다운 → (문장, 블록 골격, 표·도식 수)."""
    lines: list[Line] = []
    skeleton: list[str] = []
    visuals = 0
    section = "도입"
    in_code = False
    prev_block = None

    def block(name: str) -> None:
        nonlocal prev_block
        if name != prev_block:
            skeleton.append(name)
        prev_block = name

    def add(text: str, kind: str) -> None:
        for s in split_sentences(_md_text(text)):
            lines.append(Line(s, section, kind, len(lines) + 1))

    for raw in (md or "").splitlines():
        s = raw.strip()
        if s.startswith("```"):
            if not in_code:
                lang = s[3:].strip().lower()
                if lang in DIAGRAM_LANGS:
                    visuals += 1
                    block("diagram")
                else:
                    block("code")
            in_code = not in_code
            continue
        if in_code:
            continue
        low = s.lower()
        if "<table" in low or "<svg" in low or "<figure" in low:
            visuals += low.count("<table") + low.count("<svg") + low.count("<figure")
        if not s:
            continue
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            head = s.lstrip("#").strip()
            block(f"h{level}")
            prev_block = None
            if level <= 2:
                section = _md_text(head)[:30] or section
            add(head, "heading")
        elif s.startswith("|"):
            if _TABLE_SEP.match(s):
                visuals += 1
                continue
            block("table")
            add(" ".join(c.strip() for c in s.strip("|").split("|")), "table")
        elif s.startswith(">"):
            block("quote")
            add(s.lstrip("> "), "quote")
        elif _LIST.match(s):
            block("list")
            add(_LIST.sub("", s), "list")
        elif s.startswith("!["):
            block("image")
        else:
            block("p")
            add(s, "p")
    return lines, skeleton, visuals


def blog_doc(post: BlogPost) -> Doc:
    lines, skeleton, visuals = parse_blog(post.body_markdown)
    d = Doc(asset_id=post.id, kind="blog", week=post.week, title=post.title, description=post.meta_description,
            tags=list(post.tags), lines=lines, skeleton=skeleton, visual_blocks=visuals, obj=post)
    if post.related_video_id:
        d.sources.add(post.related_video_id)
    return d


# ---------------------------------------------------------------- 주차 묶음 읽기

def load_week(week: str, frame_kinds: set[str], families: dict[str, str]) -> tuple[list[Doc], list[str]]:
    """content/weeks/<week>/ 의 원고를 모두 읽는다. 돌려주는 값: (문서, 오류 메시지)."""
    base = paths.content_dir(week)
    plan = None
    if (base / "plan.json").exists():
        try:
            plan = read_model(base / "plan.json", WeekPlan)
        except ValidationError as e:
            return [], [f"plan.json 형식 오류: {e.errors()[0]['loc']} {e.errors()[0]['msg']} — `python -m studio plan validate --week {week}`"]
    items = {i.id: i for i in plan.items} if plan else {}
    docs: list[Doc] = []
    errors: list[str] = []
    for kind, sub in KIND_DIRS.items():
        for p in sorted((base / sub).glob("*.json")):
            try:
                obj = read_model(p, KIND_MODEL[kind])
            except ValidationError as e:
                errors.append(f"{p.stem}: 원고 형식 오류 {e.errors()[0]['loc']} {e.errors()[0]['msg']} — 먼저 `python -m studio plan validate --week {week}`")
                continue
            if isinstance(obj, VideoScript):
                doc = video_doc(obj, frame_kinds, families)
            elif isinstance(obj, CardNews):
                doc = card_doc(obj)
            else:
                doc = blog_doc(obj)
            item = items.get(doc.asset_id)
            if item:
                doc.publish_at = item.publish_at
                if item.source_id:
                    doc.sources.add(item.source_id)
            docs.append(doc)
    docs.sort(key=lambda d: d.order_key)
    return docs, errors
