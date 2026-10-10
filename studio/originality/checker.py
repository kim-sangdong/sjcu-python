"""독창성 검사 실행: 특징 → 지표·판정 → 고칠 지시(fixes) → originality.json · originality.md.

비교 규칙
  - 같은 종류끼리만 본다(쇼츠↔쇼츠, 롱폼↔롱폼 …). 종류가 다르면 형식이 달라 재사용으로 보지 않는다.
  - 묶음 내부는 발행 순서상 '앞선' 자산과만 비교한다 → 겹치면 뒤의 것을 고치게 한다(군집의 첫 편은 살린다).
  - 파생 관계(plan.source_id, CardNews.source_id, BlogPost.related_video_id)는 비교에서 뺀다.
  - 과거 아카이브: 본문·문장·화면은 전체, 제목·설명·태그·해시태그·썸네일은 최근 recent_weeks 주.
판정: 자산 = 지표 중 최악, 주간 = 자산 중 최악.
"""

from __future__ import annotations

import difflib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from studio import paths
from studio.config import Config, load_config
from studio.manifest import load_manifest
from studio.models import AssetOriginality, CheckResult, OriginalityReport, Verdict, read_model, write_model
from studio.originality import archive as A
from studio.originality import text as T
from studio.originality import visual as V
from studio.originality.assets import Doc, Line, load_week, short_label
from studio.originality.features import (
    MIN_SENT,
    TextFx,
    VisFx,
    fixed_desc_lines,
    fixed_phrases,
    text_fx,
    visual_fx,
)
from studio.originality.text import josa
from studio.originality.thresholds import Rule, Thresholds

SEVERITY: dict[str, int] = {"pass": 0, "warn": 1, "block": 2}
VIDEO_KINDS = ("longform", "short")
MIN_REUSE_SENT = 12        # 문장 재사용 판정 최소 길이(정규화 글자 수, facts S2)
ALIGN_MIN = 0.5            # 문장 n-gram 의 절반 이상이 상대 본문에 있으면 '겹친 문장'
STRUCTURE_SAME = 0.9       # 결합 신호에서 '골격 같음'
STRUCTURE_WINDOW = 14      # 골격을 비교할 과거 같은 종류 자산 수(최근 순)
CONTENT_KINDS = ["quiz", "compare", "sequence", "timeline", "flow", "bars", "bullets", "case", "quote", "text", "image", "voice"]
STYLE_HINT = {"longform": "bold·split·card·photo", "short": "bold·block·frame"}
KIND_PLATFORMS = {"longform": ("youtube", "facebook"), "short": ("youtube", "instagram", "facebook", "tiktok"),
                  "cardnews": ("instagram", "facebook"), "blog": ("tistory",)}

# 장면 kind → (이어 쓰는 꼴, 끝맺는 꼴)  — 겹친 문장이 있는 장면에 맞춰 고칠 지시를 만든다
KIND_ACTIONS: dict[str, tuple[str, str]] = {
    "title": ("첫 문장(훅)을 다른 질문으로 바꾸고", "첫 문장(훅)을 다른 질문으로 바꿀 것"),
    "quiz": ("문제·보기·정답 개념을 새로 내고", "문제·보기·정답 개념을 새로 낼 것"),
    "compare": ("비교 대상과 비교 항목을 바꾸고", "비교 대상과 비교 항목을 바꿀 것"),
    "bars": ("다른 수치 자료와 항목으로 바꾸고", "다른 수치 자료와 항목으로 바꿀 것"),
    "sequence": ("순서 맞히기 항목을 다른 사례로 바꾸고", "순서 맞히기 항목을 다른 사례로 바꿀 것"),
    "timeline": ("시간 흐름을 다른 사례로 다시 짜고", "시간 흐름을 다른 사례로 다시 짤 것"),
    "flow": ("단계 흐름을 다른 사례로 다시 짜고", "단계 흐름을 다른 사례로 다시 짤 것"),
    "bullets": ("목록 항목을 다른 관점으로 바꾸고", "목록 항목을 다른 관점으로 바꿀 것"),
    "case": ("다른 인물·상황의 사례로 바꾸고", "다른 인물·상황의 사례로 바꿀 것"),
    "quote": ("다른 인용·근거로 바꾸고", "다른 인용·근거로 바꿀 것"),
    "text": ("설명을 새 예시와 함께 다시 쓰고", "설명을 새 예시와 함께 다시 쓸 것"),
    "image": ("다른 이미지와 설명으로 바꾸고", "다른 이미지와 설명으로 바꿀 것"),
    "voice": ("새로 녹음하고", "새로 녹음할 것"),
    "chapter": ("챕터 제목을 바꾸고", "챕터 제목을 바꿀 것"),
    "cta": ("마무리 문장을 이번 편 내용에 맞게 바꾸고", "마무리 문장을 이번 편 내용에 맞게 바꿀 것"),
    "outro": ("마무리 문장을 이번 편 내용에 맞게 바꾸고", "마무리 문장을 이번 편 내용에 맞게 바꿀 것"),
    "cover": ("표지 문구를 바꾸고", "표지 문구를 바꿀 것"),
    "body": ("본문 설명을 새 예시로 다시 쓰고", "본문 설명을 새 예시로 다시 쓸 것"),
    "list": ("목록 항목을 다른 관점으로 바꾸고", "목록 항목을 다른 관점으로 바꿀 것"),
    "answer": ("정답 해설을 새로 쓰고", "정답 해설을 새로 쓸 것"),
    "p": ("문단을 새 예시·근거로 다시 쓰고", "문단을 새 예시·근거로 다시 쓸 것"),
    "table": ("표의 항목·수치를 새로 만들고", "표의 항목·수치를 새로 만들 것"),
    "heading": ("소제목을 바꾸고", "소제목을 바꿀 것"),
}
INTRO_ACTION = ("도입을 다른 사례로 바꾸고", "도입을 다른 사례로 바꿀 것")


class CheckError(RuntimeError):
    pass


@dataclass
class Past:
    """아카이브 항목 하나(이번 주보다 앞선 주)."""

    e: dict
    recent: bool

    @property
    def id(self) -> str:
        return str(self.e.get("asset_id", ""))

    @property
    def kind(self) -> str:
        return str(self.e.get("kind", ""))


class Acc:
    """자산 하나의 검사 결과 모으기."""

    def __init__(self, ck: "Checker", d: Doc):
        self.ck, self.d = ck, d
        self.checks: list[CheckResult] = []
        self.fixes: list[tuple[int, str]] = []

    def add(self, metric: str, value: float, *, detail: str = "", compared_with: str | None = None,
            fix: str | Callable[[], str | None] | None = None, rule: Rule | None = None) -> Verdict:
        rule = rule or self.ck.th.rule(metric, self.d.kind)
        verdict = rule.verdict(float(value))
        self.checks.append(CheckResult(metric=metric, value=round(float(value), 4), warn_at=rule.warn_at, block_at=rule.block_at,
                                       verdict=verdict, detail=detail, compared_with=compared_with))
        if verdict != "pass" and fix is not None:
            text = fix() if callable(fix) else fix
            if text:
                self.fixes.append((SEVERITY[verdict], text))
        return verdict

    def result(self) -> AssetOriginality:
        verdict = max((c.verdict for c in self.checks), key=SEVERITY.__getitem__, default="pass")
        fixes: list[str] = []
        for _, f in sorted(self.fixes, key=lambda x: -x[0]):
            if f not in fixes:
                fixes.append(f)
        return AssetOriginality(asset_id=self.d.asset_id, verdict=verdict, checks=self.checks, fixes=fixes)


class Checker:
    def __init__(self, week: str, stage: str, cfg: Config):
        if stage not in ("script", "render"):
            raise CheckError(f"--stage 는 script 또는 render 입니다: {stage!r}")
        self.week, self.stage, self.cfg = week, stage, cfg
        self.th = Thresholds(cfg.originality)
        frame_kinds = set(self.th.opt("body_exclude_kinds") or [])
        self.docs, errors = load_week(week, frame_kinds, dict(self.th.opt("format_families") or {}))
        if errors:
            raise CheckError("\n".join(errors))
        if not self.docs:
            raise CheckError(f"{week}: 검사할 원고가 없습니다({paths.content_dir(week)}). 원고를 먼저 쓰세요.")
        self.by_id = {d.asset_id: d for d in self.docs}
        self.order = {d.asset_id: i for i, d in enumerate(self.docs)}
        self.past = self._load_past()
        self.fixed = fixed_phrases(self.docs, [p.e for p in self.past], self.th)
        fdesc = fixed_desc_lines(self.docs, self.th)
        self.tx: dict[str, TextFx] = {d.asset_id: text_fx(d, self.fixed, fdesc, self.th) for d in self.docs}
        self.tfidf = self._tfidf()
        self.vis: dict[str, VisFx] = {}
        self._past_frames: dict[str, tuple[np.ndarray, np.ndarray, list[Past]]] = {}
        self._sig_cache: dict[tuple[str, str], tuple[list[str], np.ndarray]] = {}

    # ------------------------------------------------------------ 준비

    def _load_past(self) -> list[Past]:
        idx = A.load_index()
        mon = paths.week_monday(self.week)
        recent = int(self.th.opt("recent_weeks"))
        out = []
        for e in idx.get("assets", []):
            w = str(e.get("week") or "")
            if not w or w >= self.week or e.get("asset_id") in self.by_id:
                continue
            try:
                age = (mon - paths.week_monday(w)).days // 7
            except ValueError:
                continue
            out.append(Past(e, age <= recent))
        return out

    def _tfidf(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for kind in dict.fromkeys(d.kind for d in self.docs):
            group = [d for d in self.docs if d.kind == kind]
            texts = ["".join(T.normalize(l.text) for l in self.tx[d.asset_id].body) for d in group]
            out.update({d.asset_id: v for d, v in zip(group, T.tfidf_vectors(texts))})
        return out

    def load_visuals(self, docs: list[Doc]) -> None:
        manifest = load_manifest(self.week)
        jobs = max(1, int(self.th.opt("jobs")))
        with ThreadPoolExecutor(jobs) as ex:
            for d, vis in zip(docs, ex.map(lambda d: visual_fx(d, manifest.get(d.asset_id), self.th), docs)):
                self.vis[d.asset_id] = vis

    def mh_sims(self, kind: str, key: str, sig: list[int]) -> dict[str, float]:
        """과거 같은 종류 전체와의 MinHash 유사도를 한 번에(1년치 수백 개도 행렬 한 번)."""
        ck = (kind, key)
        if ck not in self._sig_cache:
            rows = [(p.id, p.e[key]) for p in self.past if p.kind == kind and isinstance(p.e.get(key), list) and len(p.e[key]) == len(sig)]
            mat = np.array([r for _, r in rows], dtype=np.int64) if rows else np.zeros((0, len(sig)), dtype=np.int64)
            self._sig_cache[ck] = ([i for i, _ in rows], mat)
        ids, mat = self._sig_cache[ck]
        if not ids or not sig or sig[0] == T.EMPTY_SIG:
            return {}
        eq = (mat == np.asarray(sig, dtype=np.int64)).mean(axis=1)
        eq[mat[:, 0] == T.EMPTY_SIG] = 0.0
        return dict(zip(ids, eq.tolist()))

    # ------------------------------------------------------------ 비교 대상

    def label(self, asset_id: str | None) -> str:
        return short_label(asset_id or "?", self.week)

    def earlier(self, d: Doc) -> list[Doc]:
        i = self.order[d.asset_id]
        return [o for o in self.docs[:i] if o.kind == d.kind and o.asset_id not in d.sources and d.asset_id not in o.sources]

    def past_of(self, d: Doc, *, recent_only: bool = False) -> list[Past]:
        return [p for p in self.past if p.kind == d.kind and (p.recent or not recent_only)
                and p.id not in d.sources and d.asset_id not in (p.e.get("sources") or [])]

    # ------------------------------------------------------------ 판정

    def judge(self, d: Doc) -> AssetOriginality:
        acc = Acc(self, d)
        pair: dict[str, dict[str, float]] = {}
        self._script(d, acc, pair)
        self._sentences(d, acc)
        self._structure(d, acc, pair)
        if d.kind == "short":
            self._format(d, acc)
        self._meta(d, acc, pair)
        self._human(d, acc)
        self._disclosure(d, acc)
        if d.kind == "blog":
            self._blog(d, acc)
        if self.stage == "render":
            self._visual(d, acc, pair)
        self._combo(d, acc, pair)
        return acc.result()

    # ---- 대본 본문

    def _script(self, d: Doc, acc: Acc, pair: dict) -> None:
        tx = self.tx[d.asset_id]
        best, best_num, best_tf = (0.0, None), (0.0, None), (0.0, None)
        n_batch = n_past = 0
        for o in self.earlier(d):
            ot = self.tx[o.asset_id]
            j, jn = T.jaccard(tx.sh, ot.sh), T.jaccard(tx.sh_num, ot.sh_num)
            c = T.cosine(self.tfidf[d.asset_id], self.tfidf[o.asset_id])
            pair.setdefault(o.asset_id, {}).update(script=max(j, jn), raw=j, num=jn)
            best = max(best, (j, o.asset_id), key=lambda x: x[0])
            best_num = max(best_num, (jn, o.asset_id), key=lambda x: x[0])
            best_tf = max(best_tf, (c, o.asset_id), key=lambda x: x[0])
            n_batch += 1
        sims, sims_n = self.mh_sims(d.kind, "minhash", tx.mh), self.mh_sims(d.kind, "minhash_num", tx.mh_num)
        for p in self.past_of(d):
            j, jn = sims.get(p.id, 0.0), sims_n.get(p.id, 0.0)
            pair.setdefault(p.id, {}).update(script=max(j, jn), raw=j, num=jn)
            best = max(best, (j, p.id), key=lambda x: x[0])
            best_num = max(best_num, (jn, p.id), key=lambda x: x[0])
            n_past += 1
        scope = f"본문 {sum(len(T.normalize(l.text)) for l in tx.body)}자, 비교 이번 주 {n_batch}편·과거 {n_past}편"
        raw_same = pair.get(best_num[1] or "", {}).get("raw", 0.0)
        raw_rule = self.th.rule("script_sim", d.kind)
        # 숫자를 빼면 훨씬 더 같아지는 상대 → '숫자만 바꾼 재탕' 지시 하나로 합친다
        numbers_only = raw_rule.verdict(raw_same) == "pass" or best_num[0] - raw_same >= 0.15
        same_partner = numbers_only and best_num[1] == best[1] and self.th.rule("script_sim_num", d.kind).verdict(best_num[0]) != "pass"
        acc.add("script_sim", best[0], detail=f"{self._with(best[1])}문자 {T.SHINGLE_N}-gram Jaccard ({scope})",
                compared_with=best[1], fix=None if same_partner else (lambda: self.fix_script(d, best[1], best[0], numbers=False)))
        acc.add("script_sim_num", best_num[0], detail=f"{self._with(best_num[1])}숫자를 0 으로 바꾼 뒤 Jaccard (숫자 포함 {raw_same:.2f})",
                compared_with=best_num[1],
                fix=(lambda: self.fix_script(d, best_num[1], best_num[0], numbers=True, raw=raw_same)) if numbers_only else None)
        acc.add("script_tfidf", best_tf[0], detail=f"{self._with(best_tf[1])}문자 3~5-gram TF-IDF 코사인(이번 주 같은 종류 안에서)",
                compared_with=best_tf[1], fix=lambda: self.fix_tfidf(d, best_tf[1], best_tf[0]))

    def _with(self, other: str | None) -> str:
        return f"{self.label(other)} 대비 · " if other else ""

    def overlap_lines(self, d: Doc, other: Doc, *, numbers: bool) -> list[Line]:
        osh = self.tx[other.asset_id].sh_num if numbers else self.tx[other.asset_id].sh
        out = []
        for l in self.tx[d.asset_id].body:
            s = T.shingles(T.normalize(l.text, numbers=numbers))
            if s and T.containment(s, osh) >= ALIGN_MIN:
                out.append(l)
        return out

    def exact_lines(self, d: Doc, past_id: str) -> list[Line]:
        p = next((p for p in self.past if p.id == past_id), None)
        hs = set((p.e.get("sentence_hashes") or []) if p else [])
        tx = self.tx[d.asset_id]
        body_ids = {id(l) for l in tx.body}
        return [l for l, h in zip(d.lines, tx.hashes) if h in hs and id(l) in body_ids]

    @staticmethod
    def ranges(nums: list[int]) -> str:
        nums = sorted(set(nums))
        out, start, prev = [], None, None
        for n in nums:
            if start is None:
                start = prev = n
            elif n == prev + 1:
                prev = n
            else:
                out.append(f"{start}~{prev}" if prev != start else f"{start}")
                start = prev = n
        if start is not None:
            out.append(f"{start}~{prev}" if prev != start else f"{start}")
        return "·".join(out)

    def where(self, lines: list[Line]) -> str:
        rng = self.ranges([l.no for l in lines])
        scenes = list(dict.fromkeys(l.scene_id for l in lines))
        sc = "·".join(scenes[:4]) + (" 외" if len(scenes) > 4 else "")
        return f"문장 {rng}({sc})" + ("이" if T._has_final(rng) else "가")

    def actions(self, d: Doc, lines: list[Line]) -> str:
        body = self.tx[d.asset_id].body
        acts: list[tuple[str, str]] = []
        if lines and body:
            first = next((i for i, l in enumerate(body) if l is lines[0]), 0)
            if first <= max(1, len(body) // 4):
                acts.append(INTRO_ACTION)
        for k in dict.fromkeys(l.scene_kind for l in lines):
            if k in KIND_ACTIONS and KIND_ACTIONS[k] not in acts:
                acts.append(KIND_ACTIONS[k])
        acts = acts[:3] or [("다른 사례·질문으로 다시 쓰고", "다른 사례·질문으로 다시 쓸 것")]
        return " ".join(a[0] for a in acts[:-1]) + (" " if len(acts) > 1 else "") + acts[-1][1]

    def fix_script(self, d: Doc, other_id: str | None, value: float, *, numbers: bool, raw: float = 0.0) -> str | None:
        if not other_id:
            return None
        me, other = d.label, self.label(other_id)
        o = self.by_id.get(other_id)
        lines = self.overlap_lines(d, o, numbers=numbers) if o else self.exact_lines(d, other_id)
        past = "" if o else "과거 "
        if numbers:
            loc = f" {self.where(lines)} 숫자만 다름" if lines else ""
            return (f"숫자만 바꾼 재탕 — {past}{josa(other, '와/과')} 숫자를 빼면 {value:.2f} 같음(숫자 포함 {raw:.2f}),{loc} — "
                    f"{josa(me, '은/는')} 숫자만 바꾸지 말고 {self.actions(d, lines)}")
        if lines:
            if o:
                return f"{josa(other, '와/과')} {me}의 {self.where(lines)} {value:.2f} 유사 — {josa(me, '은/는')} {self.actions(d, lines)}"
            return f"과거 {josa(other, '와/과')} 본문이 {value:.2f} 유사({self.where(lines)} 똑같음) — {josa(me, '은/는')} {self.actions(d, lines)}"
        return (f"{past}{josa(other, '와/과')} {me}의 본문이 전체적으로 {value:.2f} 유사 — {josa(me, '은/는')} 같은 주제라도 "
                f"다른 사례·질문·비교 항목으로 처음부터 다시 쓸 것(plan 의 angle 도 바꿀 것)")

    def fix_tfidf(self, d: Doc, other_id: str | None, value: float) -> str | None:
        if not other_id:
            return None
        return (f"{josa(self.label(other_id), '와/과')} 어휘·주제가 {value:.2f} 겹침(TF-IDF) — {josa(d.label, '은/는')} 다른 하위 주제나 "
                f"대상(예: 다른 연령·상황·과목)으로 각도를 바꿔 핵심어 자체를 달리 쓸 것")

    # ---- 문장

    def _sentences(self, d: Doc, acc: Acc) -> None:
        tx = self.tx[d.asset_id]
        me = d.label
        # 자산 내부 반복(숫자 유지)
        seen: dict[str, list[Line]] = {}
        total = 0
        for l, n in zip(d.lines, tx.norm):
            if len(n) >= MIN_SENT:
                total += 1
                seen.setdefault(n, []).append(l)
        dups = [v for v in seen.values() if len(v) > 1]
        extra = sum(len(v) - 1 for v in dups)
        sample = ", ".join(f"'{_clip(v[0].text)}'(문장 {'·'.join(str(x.no) for x in v)})" for v in dups[:3])
        acc.add("sentence_repeat", extra / total if total else 0.0, detail=f"반복 {extra}문장 / {total}문장" + (f": {sample}" if sample else ""),
                fix=f"{me} 안에서 같은 문장이 되풀이됨: {sample} — 한 번만 남기고 나머지는 새 정보(예시·근거·수치)로 바꿀 것")
        # 다른 자산과 똑같은 본문 문장(틀 장면·고정 문구 제외 — 인트로·CTA 공유는 고정 문구 비율로 본다)
        body_ids = {id(l) for l in tx.body}
        elig = [(l, h) for l, h, n in zip(d.lines, tx.hashes, tx.norm) if id(l) in body_ids and len(n) >= MIN_REUSE_SENT]
        partners: dict[str, list[Line]] = {}
        for o in self.earlier(d):
            oh = set(self.tx[o.asset_id].hashes)
            hit = [l for l, h in elig if h in oh]
            if hit:
                partners[o.asset_id] = hit
        for p in self.past_of(d):
            ph = set(p.e.get("sentence_hashes") or [])
            hit = [l for l, h in elig if h in ph]
            if hit:
                partners[p.id] = hit
        shared = {l.no for hit in partners.values() for l in hit}
        top = max(partners.items(), key=lambda kv: len(kv[1]), default=(None, []))
        ratio = len(shared) / len(elig) if elig else 0.0
        acc.add("sentence_reuse", ratio, detail=f"같은 문장 {len(shared)} / {len(elig)}문장" + (f", 가장 많이 겹친 상대 {self.label(top[0])}" if top[0] else ""),
                compared_with=top[0],
                fix=lambda: (f"{self.where(sorted({l.no: l for hit in partners.values() for l in hit}.values(), key=lambda x: x.no))} "
                             f"{self.label(top[0])} 등 다른 편과 똑같음 — {josa(me, '은/는')} 이 문장들을 이번 편 사례에 맞게 새로 쓸 것"))
        # 고정 문구 비율
        total_chars = sum(len(n) for n in tx.norm)
        fixed_lines = [(l, n) for l, h, n in zip(d.lines, tx.hashes, tx.norm) if h in self.fixed and len(n) >= MIN_SENT]
        fixed_chars = sum(len(n) for _, n in fixed_lines)
        ratio = fixed_chars / total_chars if total_chars else 0.0
        texts = list(dict.fromkeys(_clip(l.text) for l, _ in fixed_lines))
        sample = ", ".join(f"'{t}'" for t in texts[:3])
        warn = self.th.rule("fixed_phrase_ratio", d.kind).warn_at
        acc.add("fixed_phrase_ratio", ratio, detail=f"고정 문구 {fixed_chars}자 / {total_chars}자" + (f": {sample}" if sample else ""),
                fix=f"고정 문구(여러 편에 되풀이되는 인트로·CTA 등)가 {me} 내레이션의 {ratio:.0%} — {sample} 등을 이번 편 내용에 맞게 바꾸거나 줄여 "
                    f"{(warn or 0.1):.0%} 아래로 낮출 것")

    # ---- 구조

    def _structure(self, d: Doc, acc: Acc, pair: dict) -> None:
        sk = d.skeleton
        best = (0.0, None)
        if len(sk) >= 2:
            for o in self.earlier(d):
                r = difflib.SequenceMatcher(None, sk, o.skeleton, autojunk=False).ratio()
                pair.setdefault(o.asset_id, {})["structure"] = r
                best = max(best, (r, o.asset_id), key=lambda x: x[0])
            recent = sorted(self.past_of(d), key=lambda p: (p.e.get("week", ""), p.id))[-STRUCTURE_WINDOW:]
            for p in recent:
                r = difflib.SequenceMatcher(None, sk, p.e.get("skeleton") or [], autojunk=False).ratio()
                pair.setdefault(p.id, {})["structure"] = r
                best = max(best, (r, p.id), key=lambda x: x[0])
        shape = "→".join(sk[:12]) + ("→…" if len(sk) > 12 else "")
        unit = "슬라이드" if d.kind == "cardnews" else "블록" if d.kind == "blog" else "장면"
        acc.add("structure_sim", best[0], detail=f"{self._with(best[1])}{unit} 순서 {shape}", compared_with=best[1],
                fix=lambda: (f"{unit} 구성({shape})이 {josa(self.label(best[1]), '와/과')} 거의 같음 — {josa(d.label, '은/는')} {unit} 순서를 바꾸거나 "
                             f"다른 kind {unit}을 넣어 흐름을 다르게 짤 것"))

    # ---- 형식 순환(쇼츠)

    def _prev_week_shorts(self) -> list[tuple[str, str]]:
        prev = paths.previous_week(self.week)
        items = sorted((p.id, str(p.e.get("format") or "")) for p in self.past if p.kind == "short" and p.e.get("week") == prev)
        return items[-3:]

    def _suggest_formats(self, d: Doc, shorts: list[Doc]) -> str:
        fam = dict(self.th.opt("format_families") or {})
        counts = {}
        for k in CONTENT_KINDS:
            f = fam.get(k, k)
            counts.setdefault(f, 0)
        for x in shorts:
            if x.format in counts:
                counts[x.format] += 1
        i = shorts.index(d)
        neighbors = {x.format for x in shorts[max(0, i - 1):i + 2]}
        cand = sorted((c, f) for f, c in counts.items() if f not in neighbors)
        return ", ".join(f"{f}({c}편)" for c, f in cand[:3])

    def _format(self, d: Doc, acc: Acc) -> None:
        shorts = [x for x in self.docs if x.kind == "short"]
        prev = self._prev_week_shorts()
        seq = [(pid, f) for pid, f in prev] + [(x.asset_id, x.format or "") for x in shorts]
        i = len(prev) + shorts.index(d)
        run_ids = []
        j = i - 1
        while j >= 0 and seq[j][1] == d.format:
            run_ids.insert(0, seq[j][0])
            j -= 1
        run = len(run_ids) + 1
        mix = ", ".join(f"{k} {v:.0%}" for k, v in sorted(d.format_mix.items(), key=lambda kv: -kv[1])[:4])
        declared = f"; notes 선언 '{d.declared_format}'" if d.declared_format else ""
        me = d.label
        labels = "·".join(self.label(x) for x in run_ids)
        acc.add("format_run", run, detail=f"대표 형식 {d.format} (장면 비중 {mix}{declared})" + (f", 연속: {labels}→{me}" if run_ids else ""),
                compared_with=run_ids[-1] if run_ids else None,
                fix=lambda: (f"{josa(me, '이/가')} {labels}에 이어 같은 형식({d.format})으로 {run}편 연속 — {me}의 주 장면을 이번 주에 적게 쓴 형식"
                             f"({self._suggest_formats(d, shorts)})으로 바꾸거나 plan 의 발행 순서를 바꿀 것"
                             f"(형식 = 틀 장면을 뺀 장면 kind 중 화면 시간 비중이 가장 큰 것)"))
        count = 1 + sum(1 for x in shorts[:shorts.index(d)] if x.format == d.format)
        same = [x.asset_id for x in shorts if x.format == d.format]
        warn = self.th.rule("format_count", "short").warn_at
        acc.add("format_count", count, detail=f"이번 주 형식 {d.format} {len(same)}편 중 {count}번째: {'·'.join(self.label(x) for x in same)}",
                fix=lambda: (f"형식 {josa(d.format or '?', '이/가')} 이번 주 {count}번째(기준 주 {int((warn or 4) - 1)}편 이하) — {me}의 주 장면을 "
                             f"{self._suggest_formats(d, shorts)} 중 하나로 바꿀 것"))

    # ---- 메타데이터

    def _meta(self, d: Doc, acc: Acc, pair: dict) -> None:
        tx = self.tx[d.asset_id]
        me = d.label
        earlier = self.earlier(d)
        recent = self.past_of(d, recent_only=True)
        # 제목 유사도
        best = (0.0, None, "")
        for o in earlier:
            s = T.jaccard(tx.title_grams, self.tx[o.asset_id].title_grams)
            pair.setdefault(o.asset_id, {})["title"] = s
            best = max(best, (s, o.asset_id, o.title), key=lambda x: x[0])
        for p in recent:
            s = T.jaccard(tx.title_grams, T.title_grams(str(p.e.get("title") or "")))
            pair.setdefault(p.id, {})["title"] = s
            best = max(best, (s, p.id, str(p.e.get("title") or "")), key=lambda x: x[0])
        acc.add("title_sim", best[0], detail=f"{self._with(best[1])}'{_clip(best[2], 40)}'" if best[1] else "비교 대상 없음", compared_with=best[1],
                fix=lambda: f"제목이 {self.label(best[1])} '{_clip(best[2], 40)}'{'과' if T._has_final(best[2]) else '와'} {best[0]:.2f} 비슷함 — "
                            f"{josa(me, '은/는')} 다른 핵심어·관점으로 제목을 새로 쓸 것")
        # 제목 앞 3어절
        window = int(self.th.opt("title_prefix_window"))
        nwords = int(self.th.opt("title_prefix_words"))
        prior = [(p.id, str(p.e.get("title_head") or T.title_head(str(p.e.get("title") or ""), nwords)))
                 for p in sorted(recent, key=lambda p: (p.e.get("week", ""), p.id))]
        prior += [(o.asset_id, self.tx[o.asset_id].head) for o in earlier]
        prior = prior[-window:]
        hits = [pid for pid, h in prior if tx.head and len(tx.head.split()) >= 2 and h == tx.head]
        acc.add("title_prefix", len(hits), detail=f"앞 {nwords}어절 '{tx.head}', 직전 {len(prior)}편과 비교" + (f": {'·'.join(self.label(h) for h in hits)}" if hits else ""),
                compared_with=hits[-1] if hits else None,
                fix=lambda: (f"제목 앞 {nwords}어절 '{tx.head}'{'이' if T._has_final(tx.head) else '가'} 직전 {window}편 중 "
                             f"{josa('·'.join(self.label(h) for h in hits), '와/과')} 같음 — {me}의 제목 첫머리를 다른 말로 시작할 것"))
        # 연번 제목
        mark = T.serial_marker(d.title)
        acc.add("serial_title", 1 if mark else 0, detail=f"'{mark}'" if mark else "",
                fix=f"제목의 연번 표현 '{mark}'{'을' if mark and T._has_final(mark) else '를'} 빼고 이번 편 내용이 드러나는 제목으로 바꿀 것")
        # 설명 유사도
        best = (0.0, None)
        if tx.desc_len >= 20:
            for o in earlier:
                if self.tx[o.asset_id].desc_len >= 20:
                    best = max(best, (T.jaccard(tx.desc_sh, self.tx[o.asset_id].desc_sh), o.asset_id), key=lambda x: x[0])
            dsims = self.mh_sims(d.kind, "desc_minhash", tx.desc_mh)
            for p in recent:
                best = max(best, (dsims.get(p.id, 0.0), p.id), key=lambda x: x[0])
        acc.add("desc_sim", best[0], detail=f"{self._with(best[1])}설명 본문 {tx.desc_len}자(링크·해시태그·반복 줄 제외)", compared_with=best[1],
                fix=lambda: (f"설명 본문이 {josa(self.label(best[1]), '와/과')} {best[0]:.2f} 같음 — {me}의 설명 첫 두세 문장을 이번 편 내용 요약으로 "
                             f"새로 쓸 것(앱 링크·고지 같은 고정 줄은 그대로 둬도 된다)"))
        # 태그
        best = (0.0, None)
        if len(tx.tags) >= 3:
            for o in earlier:
                if len(self.tx[o.asset_id].tags) >= 3:
                    best = max(best, (T.jaccard(set(tx.tags), set(self.tx[o.asset_id].tags)), o.asset_id), key=lambda x: x[0])
            for p in recent:
                pt = set(p.e.get("tags") or [])
                if len(pt) >= 3:
                    best = max(best, (T.jaccard(set(tx.tags), pt), p.id), key=lambda x: x[0])
        acc.add("tag_sim", best[0], detail=f"{self._with(best[1])}태그 {len(tx.tags)}개", compared_with=best[1],
                fix=lambda: f"태그가 {josa(self.label(best[1]), '와/과')} {best[0]:.2f} 겹침 — {me}만의 키워드 태그를 3개 이상 넣고 공통 태그는 줄일 것")
        # 해시태그 세트
        same: list[str] = []
        if len(tx.hashtags) >= int(self.th.opt("hashtag_min_set")):
            same = [o.asset_id for o in earlier if self.tx[o.asset_id].hashtags == tx.hashtags]
            same += [p.id for p in recent if frozenset(p.e.get("hashtags") or []) == tx.hashtags]
        tags = " ".join("#" + t for t in sorted(tx.hashtags))
        acc.add("hashtag_same", len(same), detail=f"{tags}" + (f" = {'·'.join(self.label(s) for s in same[:5])}" if same else ""),
                compared_with=same[-1] if same else None,
                fix=lambda: f"해시태그 세트({tags})가 {josa(self.label(same[-1]), '와/과')} 완전히 같음 — {me}의 주제에 맞는 해시태그로 2개 이상 바꿀 것")

    # ---- 사람 개입

    def _human(self, d: Doc, acc: Acc) -> None:
        me = d.label
        if d.kind == "longform":
            acc.add("voice_scenes", d.voice_scenes, detail=f"voice 장면 {d.voice_scenes}개",
                    fix=f"{me}에 운영자 육성이 없음 — 직접 녹음한 파일을 params.audio 로 넣은 voice 장면을 넣을 것(합계 2분 이상 권장)")
            vis = self.vis.get(d.asset_id)
            if self.stage == "render" and vis and vis.asset is not None:
                secs = float(vis.asset.meta.get("human_voice_seconds") or 0.0)
                need = self.th.rule("voice_seconds", d.kind).warn_at or self.th.rule("voice_seconds", d.kind).block_at or 0
                acc.add("voice_seconds", secs, detail=f"렌더된 사람 녹음 {secs:.0f}초",
                        fix=f"{me}의 사람 녹음이 {secs:.0f}초 — 이번 주 새로 녹음한 voice 장면을 늘려 {need:.0f}초 이상으로 할 것")
        if d.kind == "short":
            shorts = [x for x in self.docs if x.kind == "short"]
            n = sum(1 for x in shorts if x.voice_scenes > 0)
            base = self.th.rule("voice_week", "short")
            need = int(base.warn_at if base.warn_at is not None else (base.block_at or 0))
            if d.voice_scenes == 0:
                silent = [x for x in shorts if x.voice_scenes == 0]
                chosen = {x.asset_id for x in silent[-max(0, need - n):]} if need > n else set()
                rule = base if d.asset_id in chosen else Rule(None, None, base.cmp)
                acc.add("voice_week", n, detail=f"이번 주 육성 쇼츠 {n}편(기준 {need}편)", rule=rule,
                        fix=f"이번 주 육성(voice 장면) 쇼츠가 {n}편으로 기준 {need}편보다 적음 — {me}에 운영자가 직접 녹음한 voice 장면을 넣을 것"
                            f"(모자란 {need - n}편 중 하나)")
        # 녹음 파일 재사용
        tx = self.tx[d.asset_id]
        if tx.voice_hashes:
            used: dict[str, str] = {}
            for o in self.docs[:self.order[d.asset_id]]:
                for h in self.tx[o.asset_id].voice_hashes:
                    used.setdefault(h, o.asset_id)
            for p in self.past:
                for h in p.e.get("voice_hashes") or []:
                    used.setdefault(h, p.id)
            hits = [(h, used[h]) for h in tx.voice_hashes if h in used]
            acc.add("voice_reuse", len(hits), detail=f"녹음 파일 {len(tx.voice_hashes)}개" + (f", 재사용: {'·'.join(self.label(x) for _, x in hits)}" if hits else ""),
                    compared_with=hits[0][1] if hits else None,
                    fix=lambda: f"{me}의 녹음 파일이 {josa(self.label(hits[0][1]), '이/가')} 쓴 것과 같음 — 이번 편을 위해 새로 녹음할 것")

    # ---- AI 고지

    def _disclosure(self, d: Doc, acc: Acc) -> None:
        me = d.label
        if d.kind in VIDEO_KINDS:
            tts_lines = sum(1 for l in d.lines if l.scene_kind != "voice")
            provider = None
            vis = self.vis.get(d.asset_id)
            if vis and vis.asset is not None:
                provider = vis.asset.meta.get("tts_provider")
            missing = tts_lines > 0 and not d.synthetic_media
            acc.add("ai_disclosure", 1 if missing else 0,
                    detail=f"합성 음성 문장 {tts_lines}개, synthetic_media={str(d.synthetic_media).lower()}" + (f", TTS {provider}" if provider else ""),
                    fix=f"{josa(me, '은/는')} 합성 음성(TTS) 내레이션을 쓰는데 synthetic_media=false — true 로 바꿀 것(플랫폼 AI 표시와 설명란 AI 고지가 발행 때 자동으로 붙는다)")
        elif d.kind == "cardnews":
            words = [str(w).lower() for w in self.th.opt("disclosure_words") or []]
            has = any(w in (d.description or "").lower() for w in words)
            missing = bool(d.synthetic_media) and not has
            acc.add("ai_disclosure", 1 if missing else 0, detail=f"synthetic_media={str(d.synthetic_media).lower()}, 캡션 고지 {'있음' if has else '없음'}",
                    fix=f"{josa(me, '은/는')} synthetic_media=true 인데 캡션에 AI 사용 고지가 없음(카드뉴스 캡션에는 자동으로 붙지 않는다) — 캡션 끝에 "
                        f"'※ 이 카드뉴스의 이미지는 AI로 만들었습니다.' 같은 문장을 넣을 것")

    # ---- 블로그

    def _blog(self, d: Doc, acc: Acc) -> None:
        count = d.visual_blocks or 0
        src = "원고"
        vis = self.vis.get(d.asset_id)
        if vis and vis.asset is not None and "table_count" in vis.asset.meta:
            count = int(vis.asset.meta.get("table_count") or 0) + int(vis.asset.meta.get("diagram_count") or 0)
            src = "렌더"
        acc.add("blog_visuals", count, detail=f"표·도식 {count}개({src} 기준)",
                fix=f"{d.label}에 이 글만의 표·도식이 없음 — 비교 표(마크다운 표)나 ```flow 도식을 1개 이상 넣을 것")

    # ---- 시각(render)

    def _past_frame_bank(self, kind: str) -> tuple[np.ndarray, np.ndarray, list[Past]]:
        if kind not in self._past_frames:
            owners, chunks, starts, pos = [], [], [], 0
            for p in self.past:
                if p.kind != kind or not p.e.get("frame_phashes"):
                    continue
                w = V.words(p.e["frame_phashes"])
                owners.append(p)
                chunks.append(w)
                starts.append(pos)
                pos += len(w)
            bank = np.concatenate(chunks) if chunks else np.zeros((0, 4), dtype=np.uint64)
            self._past_frames[kind] = (bank, np.array(starts, dtype=np.int64), owners)
        return self._past_frames[kind]

    def _visual(self, d: Doc, acc: Acc, pair: dict) -> None:
        me = d.label
        vis = self.vis.get(d.asset_id)
        cmd = {"longform": "video", "short": "video", "cardnews": "cardnews", "blog": "blog"}[d.kind]
        if vis is None or vis.missing:
            if not any(getattr(getattr(self.cfg.platforms, pf, None), "enabled", True) for pf in KIND_PLATFORMS[d.kind]):
                acc.add("render_missing", 0, detail="렌더 결과 없음 — 이 종류를 올릴 플랫폼이 모두 꺼져 있어 판정하지 않음")
                return
            what = ", ".join(vis.missing) if vis else "manifest"
            acc.add("render_missing", 1, detail=f"없음: {what}",
                    fix=f"{me}의 렌더 결과가 없음({what}) — `python -m studio render {cmd} --week {self.week} --only {d.asset_id}` 로 렌더한 뒤 다시 검사할 것")
            return
        if vis.error:
            acc.add("render_missing", 1, detail=f"렌더 파일을 읽지 못함: {vis.error[:200]}",
                    fix=f"{me}의 렌더 파일이 깨져 있음 — `python -m studio render {cmd} --week {self.week} --only {d.asset_id}` 로 다시 렌더할 것")
            return
        acc.add("render_missing", 0, detail=f"표본 방식 {vis.mode}" if vis.mode else "")
        # 썸네일
        if vis.thumb is not None:
            best = (256, None)
            for o in self.earlier(d):
                ov = self.vis.get(o.asset_id)
                if ov and ov.thumb:
                    dist = V.thumb_distance(vis.thumb, ov.thumb.phash, ov.thumb.edge)
                    pair.setdefault(o.asset_id, {})["thumb"] = dist
                    best = min(best, (dist, o.asset_id), key=lambda x: x[0])
            for p in self.past_of(d, recent_only=True):
                if p.e.get("thumb_phash"):
                    edge = p.e.get("thumb_edge_phash")
                    dist = V.thumb_distance(vis.thumb, int(p.e["thumb_phash"], 16), int(edge, 16) if edge else None)
                    pair.setdefault(p.id, {})["thumb"] = dist
                    best = min(best, (dist, p.id), key=lambda x: x[0])
            what = {"longform": "썸네일", "short": "커버", "cardnews": "표지 슬라이드", "blog": "대표 이미지(og)"}[d.kind]
            hint = (f"thumbnail.style 을 다른 것({STYLE_HINT[d.kind]})으로 바꾸거나 " if d.kind in STYLE_HINT else "")
            acc.add("thumb_dist", best[0], detail=f"{self._with(best[1])}{what} 256비트 pHash 해밍거리(원본·색 반전·윤곽선 중 최소)" if best[1] else "비교 대상 없음",
                    compared_with=best[1],
                    fix=lambda: (f"{josa(what, '이/가')} {josa(self.label(best[1]), '와/과')} 거의 같음(거리 {best[0]}/256) — 글자만 바꾸지 말고 {hint}"
                                 f"배치·색·핵심 그림을 바꿀 것"))
        # 화면 소재 재사용
        if vis.frames is not None and d.kind in ("longform", "short", "cardnews"):
            q = vis.words
            bits = int(self.th.opt("frame_match_bits"))
            best = (0.0, None, np.zeros(len(q), dtype=bool))
            if len(q):
                for o in self.earlier(d):
                    ov = self.vis.get(o.asset_id)
                    if ov is None or ov.frames is None or not ov.frames.hexes:
                        continue
                    hit = V.match_matrix(q, ov.words, bits).any(axis=1)
                    r = float(hit.mean())
                    pair.setdefault(o.asset_id, {})["frame"] = r
                    if r > best[0]:
                        best = (r, o.asset_id, hit)
                bank, starts, owners = self._past_frame_bank(d.kind)
                if len(bank):
                    m = V.match_matrix(q, bank, bits)
                    per = np.logical_or.reduceat(m, starts, axis=1)
                    for k, p in enumerate(owners):
                        if p.id in d.sources or d.asset_id in (p.e.get("sources") or []):
                            continue
                        hit = per[:, k]
                        r = float(hit.mean())
                        if r > 0:
                            pair.setdefault(p.id, {})["frame"] = r
                        if r > best[0]:
                            best = (r, p.id, hit)
            n, k = len(q), int(best[2].sum())
            times = [t for t, h in zip(vis.frames.times, best[2]) if h]
            unit = "슬라이드" if d.kind == "cardnews" else "화면 표본"
            acc.add("frame_reuse", best[0], detail=f"{self._with(best[1])}{unit} {n}장 중 {k}장 일치(256비트 해밍 ≤{bits})", compared_with=best[1],
                    fix=lambda: self.fix_frames(d, best[1], best[0], n, k, times))
        # 정지 화면
        if vis.freeze is not None:
            fz = vis.freeze
            a, b = fz.longest_span
            scenes = [s for s in vis.scenes if s["start"] < b and s["end"] > a]
            sc = "·".join(f"{s['id']}({s['kind']})" for s in scenes[:3])
            span = f"{V.mmss(a)}~{V.mmss(b)}" + (f", 장면 {sc}" if sc else "")
            acc.add("freeze_ratio", fz.ratio, detail=f"2초 이상 정지 구간 {len(fz.runs)}개, 표본 {vis.mode}",
                    fix=f"{me}의 정지 화면이 길이의 {fz.ratio:.0%} — 문장마다 화면 요소가 새로 드러나는 장면(bullets·timeline·flow·compare)을 늘리고 "
                        f"글만 있는 text·quote·image 장면과 긴 hold_seconds 를 줄일 것")
            acc.add("longest_freeze", fz.longest, detail=f"{span}",
                    fix=f"{me}의 최장 정지 {fz.longest:.0f}초({span}) — 그 장면의 narration 을 더 잘게 나눠 화면이 단계별로 바뀌게 하거나 "
                        f"hold_seconds 를 줄이고 장면을 둘로 나눌 것")

    def fix_frames(self, d: Doc, other_id: str | None, ratio: float, n: int, k: int, times: list[float]) -> str | None:
        if not other_id:
            return None
        if d.kind == "cardnews":
            return (f"{d.label}의 슬라이드 {self.ranges([int(t) for t in times])}번이 {self.label(other_id)}의 슬라이드와 같음 — "
                    f"새 이미지·배치로 다시 만들 것")
        spans: list[list[float]] = []
        for t in times:
            if spans and t - spans[-1][1] <= 3.0:
                spans[-1][1] = t
            else:
                spans.append([t, t])
        where = ", ".join(f"{V.mmss(a)}~{V.mmss(b + 1)}" for a, b in spans[:4])
        return (f"{d.label}의 화면 표본 {n}장 중 {k}장({ratio:.0%})이 {self.label(other_id)}의 화면과 같음({where}) — 같은 이미지·도식·화면 구성을 "
                f"다시 쓰지 말고 이 구간 장면을 새로 만들 것")

    # ---- 결합 신호

    def _combo(self, d: Doc, acc: Acc, pair: dict) -> None:
        th = self.th
        sw = th.rule("script_sim", d.kind).warn_at
        st = th.rule("structure_sim", d.kind).warn_at or STRUCTURE_SAME
        tw = th.rule("title_sim", d.kind).warn_at
        thw = th.rule("thumb_dist", d.kind).warn_at
        fw = th.rule("frame_reuse", d.kind).warn_at
        best: tuple[int, str | None, list[str]] = (0, None, [])
        for oid, v in pair.items():
            sig = []
            if v.get("structure", 0.0) >= st:
                sig.append("구성")
            if sw is not None and v.get("script", 0.0) >= sw:
                sig.append("본문")
            if self.stage == "render":
                if (thw is not None and v.get("thumb", 999) <= thw) or (fw is not None and v.get("frame", 0.0) >= fw):
                    sig.append("썸네일·화면")
            elif tw is not None and v.get("title", 0.0) >= tw:
                sig.append("제목")
            if len(sig) > best[0]:
                best = (len(sig), oid, sig)
        sig_text = "·".join(best[2])
        acc.add("template_combo", best[0], detail=f"{self._with(best[1])}{sig_text} 동시 유사" if best[1] else "",
                compared_with=best[1],
                fix=lambda: (f"{josa(self.label(best[1]), '와/과')} {sig_text}{'이' if T._has_final(sig_text) else '가'} 함께 비슷해 템플릿으로 찍어낸 것처럼 "
                             f"보임 — {josa(d.label, '은/는')} 장면 구성·도입 사례·{'썸네일 배치' if self.stage == 'render' else '제목 문형'} 중 둘 이상을 바꿀 것"))


def _clip(text: str, n: int = 24) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1] + "…"


# ---------------------------------------------------------------- 실행

def report_paths(week: str) -> tuple[Path, Path]:
    d = paths.content_dir(week)
    return d / "originality.json", d / "originality.md"


def run_check(week: str, stage: str = "script", *, only: list[str] | None = None, cfg: Config | None = None,
              write: bool = True) -> tuple[OriginalityReport, Checker, float]:
    """검사하고 (보고서, 검사기, 걸린 초)를 돌려준다. write=True 면 originality.json·md 를 쓴다."""
    from studio.originality.report import render_markdown

    t0 = time.time()
    cfg = cfg or load_config()
    ck = Checker(week, stage, cfg)
    only_set = set(only or [])
    targets = [d for d in ck.docs if not only_set or d.asset_id in only_set]
    if only_set - {d.asset_id for d in targets}:
        raise CheckError(f"원고를 찾지 못한 ID: {', '.join(sorted(only_set - {d.asset_id for d in targets}))}")
    if stage == "render":
        last = {}
        for t in targets:
            last[t.kind] = max(last.get(t.kind, -1), ck.order[t.asset_id])
        ck.load_visuals([d for d in ck.docs if d.kind in last and ck.order[d.asset_id] <= last[d.kind]])
    results = {d.asset_id: ck.judge(d) for d in targets}
    json_p, md_p = report_paths(week)
    if only_set and json_p.exists():
        try:
            old = read_model(json_p, OriginalityReport)
            for a in old.assets:
                if a.asset_id in ck.by_id and a.asset_id not in results:
                    results[a.asset_id] = a
        except ValueError:
            pass
    assets = [results[d.asset_id] for d in ck.docs if d.asset_id in results]
    verdict = max((a.verdict for a in assets), key=SEVERITY.__getitem__, default="pass")
    report = OriginalityReport(week=week, generated_at=datetime.now(paths.KST), stage=stage, verdict=verdict, assets=assets)
    elapsed = time.time() - t0
    if write:
        write_model(json_p, report)
        md_p.write_text(render_markdown(report, ck, elapsed, only=sorted(only_set)), encoding="utf-8")
    return report, ck, elapsed
