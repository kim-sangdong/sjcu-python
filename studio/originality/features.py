"""자산 하나에서 비교용 특징을 뽑는다(검사와 아카이브가 같이 쓴다).

텍스트: 문장 해시, 고정 문구를 뺀 본문 n-gram·MinHash, 설명 본문, 제목 2-gram·앞 3어절, 태그·해시태그 집합, 녹음 파일 해시
시각:   썸네일 pHash(원본·반전·윤곽선), 프레임 표본 pHash, 정지 구간, 렌더 타임라인 장면
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from studio import paths
from studio.models import RenderedAsset
from studio.originality import text as T
from studio.originality import visual as V
from studio.originality.assets import Doc, Line
from studio.originality.thresholds import Thresholds

MIN_SENT = 6          # 고정 문구·반복 판정에 쓰는 최소 문장 길이(정규화 글자 수)
MAX_PHRASES = 600     # 아카이브에 남길 문장 해시 상한(롱폼)
THUMB_ROLES = {"longform": ("thumbnail", "cover"), "short": ("cover", "thumbnail"), "cardnews": ("slide01",), "blog": ("og",)}


@dataclass
class TextFx:
    norm: list[str]                 # 문장별 정규화(숫자 유지) — 자산 내부 반복
    hashes: list[str]               # 문장별 해시(숫자→0) — 고정 문구·문장 재사용
    body: list[Line]                # 본문 문장(틀 장면·고정 문구 제외)
    sh: set[str]
    sh_num: set[str]
    mh: list[int]
    mh_num: list[int]
    desc_sh: set[str]
    desc_mh: list[int]
    desc_len: int
    title_grams: set[str]
    head: str
    hashtags: frozenset[str]
    tags: frozenset[str]
    voice_hashes: list[str] = field(default_factory=list)
    voice_missing: list[str] = field(default_factory=list)

    def phrase_set(self) -> set[str]:
        return {h for h, n in zip(self.hashes, self.norm) if len(n) >= MIN_SENT}


def sentence_keys(doc: Doc) -> tuple[list[str], list[str]]:
    norm = [T.normalize(l.text) for l in doc.lines]
    hashes = [T.sentence_hash(l.text) for l in doc.lines]
    return norm, hashes


def fixed_phrases(docs: list[Doc], past: list[dict], th: Thresholds) -> set[str]:
    """여러 자산(묶음+아카이브)에 되풀이되는 문장 해시 + 설정된 상용구."""
    counts: dict[str, int] = {}
    for d in docs:
        norm, hashes = sentence_keys(d)
        for h in {h for h, n in zip(hashes, norm) if len(n) >= MIN_SENT}:
            counts[h] = counts.get(h, 0) + 1
    for e in past:
        for h in set(e.get("phrase_hashes") or []):
            counts[h] = counts.get(h, 0) + 1
    need = int(th.opt("fixed_min_assets"))
    fixed = {h for h, c in counts.items() if c >= need}
    for s in th.opt("boilerplate") or []:
        for part in T.split_sentences(str(s)):
            fixed.add(T.sentence_hash(part))
    return fixed


def fixed_desc_lines(docs: list[Doc], th: Thresholds) -> set[str]:
    """여러 자산 설명란에 되풀이되는 줄(앱 링크 안내·고정 CTA 등)."""
    counts: dict[str, int] = {}
    for d in docs:
        for key in {T.normalize(x, numbers=True) for x in T.description_lines(d.description)}:
            counts[key] = counts.get(key, 0) + 1
    need = int(th.opt("fixed_min_assets"))
    return {k for k, c in counts.items() if c >= need}


def file_hash(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def text_fx(doc: Doc, fixed: set[str], fixed_desc: set[str], th: Thresholds) -> TextFx:
    norm, hashes = sentence_keys(doc)
    exclude = set(th.opt("body_exclude_kinds") or [])
    body = [l for l, h in zip(doc.lines, hashes) if l.scene_kind not in exclude and h not in fixed]
    body_raw = "".join(T.normalize(l.text) for l in body)
    body_num = "".join(T.normalize(l.text, numbers=True) for l in body)
    sh, sh_num = T.shingles(body_raw), T.shingles(body_num)
    desc = "".join(T.normalize(x, numbers=True) for x in T.description_lines(doc.description)
                   if T.normalize(x, numbers=True) not in fixed_desc)
    desc_sh = T.shingles(desc)
    voice_hashes, voice_missing = [], []
    for p in doc.voice_files:
        if p.exists():
            voice_hashes.append(file_hash(p))
        else:
            voice_missing.append(str(p))
    return TextFx(
        norm=norm, hashes=hashes, body=body, sh=sh, sh_num=sh_num,
        mh=T.minhash(sh), mh_num=T.minhash(sh_num),
        desc_sh=desc_sh, desc_mh=T.minhash(desc_sh, T.DESC_NUM_PERM), desc_len=len(desc),
        title_grams=T.title_grams(doc.title), head=T.title_head(doc.title, int(th.opt("title_prefix_words"))),
        hashtags=T.tag_set(doc.hashtags), tags=T.tag_set(doc.tags),
        voice_hashes=voice_hashes, voice_missing=voice_missing,
    )


# ---------------------------------------------------------------- 시각

@dataclass
class VisFx:
    asset: RenderedAsset | None
    missing: list[str] = field(default_factory=list)   # 없는 렌더 파일(역할)
    thumb: V.ThumbHash | None = None
    frames: V.FrameHashes | None = None
    freeze: V.FreezeStats | None = None
    duration: float | None = None
    scenes: list[dict] = field(default_factory=list)
    mode: str = ""
    error: str | None = None

    @property
    def words(self) -> np.ndarray:
        return self.frames.words if self.frames and self.frames.hexes else np.zeros((0, 4), dtype=np.uint64)


def resolve(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else paths.ROOT / p


def required_roles(doc: Doc) -> list[str]:
    if doc.kind in ("longform", "short"):
        return ["video"]
    if doc.kind == "cardnews":
        return ["slide01"]
    return ["html"]


def visual_fx(doc: Doc, asset: RenderedAsset | None, th: Thresholds, *, freeze: bool = True) -> VisFx:
    """렌더 결과에서 시각 특징을 뽑는다. 파일이 없으면 missing 에 역할을 적는다."""
    vis = VisFx(asset)
    if asset is None:
        vis.missing = ["manifest"]
        return vis
    files = {k: resolve(v) for k, v in asset.files.items()}
    vis.missing = [r for r in required_roles(doc) if r not in files or not files[r].exists()]
    try:
        for role in THUMB_ROLES.get(doc.kind, ()):
            if role in files and files[role].exists():
                vis.thumb = V.thumb_hash(files[role])
                break
        if doc.kind in ("longform", "short") and "video" in files and files["video"].exists():
            vis.scenes = V.timeline_scenes(files.get("timeline"))
            info = V.probe(files["video"])
            vis.duration = info.duration
            sample = V.decode(files["video"], info, fps=2.0 if doc.kind == "short" else 1.0)
            vis.mode = sample.mode
            skip = set(th.opt("frame_exclude_kinds") or [])
            exclude = [(s["start"], s["end"]) for s in vis.scenes if s["kind"] in skip]
            max_frames = int(th.opt("longform_max_frames")) if doc.kind == "longform" else 10_000
            vis.frames = V.frame_hashes(sample, max_frames=max_frames, exclude=exclude)
            if freeze:
                vis.freeze = V.freeze_stats(sample, float(th.opt("still_diff")), float(th.opt("min_freeze_seconds")))
        elif doc.kind == "cardnews":
            slides = sorted((k, p) for k, p in files.items() if k.startswith("slide") and p.exists())
            kinds = [s.kind for s in doc.obj.slides] if doc.obj is not None else []
            skip = set(th.opt("frame_exclude_kinds") or []) | {"cta"}
            keep = [p for i, (_, p) in enumerate(slides) if not (i < len(kinds) and kinds[i] in skip)]
            vis.frames = V.image_hashes(keep)
    except (V.MediaError, OSError) as e:
        vis.error = str(e)
    return vis
