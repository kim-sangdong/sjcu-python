"""지표별 기본 임계값과 검사 옵션.

channel.yaml 의 `originality:` 딕셔너리로 덮어쓴다.

    originality:
      script_sim: {warn_at: 0.35, block_at: 0.55}     # 모든 종류
      longest_freeze: {short: {warn_at: 10}}          # 종류별(longform | short | cardnews | blog)
      title_sim: [0.5, 0.7]                           # [warn_at, block_at]
      tag_sim: off                                    # 끄기(off 또는 null)
      require_voice: true                             # 옵션(아래 OPTIONS)
      boilerplate: ["매일 한 문제씩 앱에서 풀어 보세요."]

근거 표시: [실험] 이 저장소에서 합성·실제 렌더로 측정 / [검증] 공개 원문 확인 / [정책] 플랫폼 정책 문구(원문 미확인)
           [추정] 근거가 약한 초기값 — 실운영 4~8주 뒤 "정상적으로 서로 다른" 자산 쌍의 p95 로 다시 맞춘다.
자세한 근거는 docs/research/facts-originality.md 7.4절과 judge.md 6장 위험 2.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from studio.models import Verdict

Cmp = Literal["ge", "le", "lt"]


@dataclass(frozen=True)
class Rule:
    """warn_at / block_at 과 비교 방향.

    ge: 값이 기준 이상이면 걸림(유사도처럼 클수록 나쁨)
    le: 값이 기준 이하이면 걸림(해밍거리처럼 작을수록 나쁨)
    lt: 값이 기준 미만이면 걸림(최소 요건)
    """

    warn_at: float | None
    block_at: float | None
    cmp: Cmp = "ge"

    def hit(self, value: float, at: float | None) -> bool:
        if at is None:
            return False
        if self.cmp == "ge":
            return value >= at
        if self.cmp == "le":
            return value <= at
        return value < at

    def verdict(self, value: float) -> Verdict:
        if self.hit(value, self.block_at):
            return "block"
        if self.hit(value, self.warn_at):
            return "warn"
        return "pass"

    def describe(self) -> str:
        sym = {"ge": "≥", "le": "≤", "lt": "<"}[self.cmp]
        parts = []
        if self.warn_at is not None:
            parts.append(f"경고 {sym}{_num(self.warn_at)}")
        if self.block_at is not None:
            parts.append(f"차단 {sym}{_num(self.block_at)}")
        return ", ".join(parts) or "판정 없음"


def _num(v: float) -> str:
    return f"{v:g}"


OFF = Rule(None, None)

# 지표 이름 → Rule 또는 {종류: Rule, "*": Rule}
DEFAULTS: dict[str, Rule | dict[str, Rule]] = {
    # ── 대본 본문(같은 종류끼리, 묶음 내부는 발행 순서상 앞선 것과, 과거는 아카이브 전체와)
    # 문자 5-gram Jaccard. [실험] facts-originality 5절: 다른 주제·같은 인트로 0.16, 30% 수정본 0.68,
    # 순서만 섞음 0.81, 같은 주제 새로 씀 0.01. 이 저장소 합성 원고: 서로 다른 쇼츠 0.00~0.03. 차단 0.50 은 업계 0.7 보다 엄격 [추정]
    "script_sim": Rule(0.30, 0.50),
    # 숫자를 모두 0 으로 바꾼 뒤의 같은 값 — 숫자만 바꾼 재탕 [추정]
    "script_sim_num": Rule(0.30, 0.50),
    # 문자 3~5-gram TF-IDF 코사인(묶음 내부만 — 아카이브에는 원문을 두지 않는다). [실험] 30% 수정본 0.81, 새로 씀 0.13 / 차단 [추정]
    "script_tfidf": Rule(0.60, 0.80),
    # 한 자산 안에서 같은 문장이 되풀이되는 비율 [정책: 반복적·무성의 콘텐츠] [추정]
    "sentence_repeat": Rule(0.10, 0.25),
    # 다른 자산(묶음·과거)과 똑같은 문장 비율. 고정 문구는 빼고 센다 [추정]
    "sentence_reuse": Rule(0.10, 0.25),
    # 고정 문구(여러 자산에 되풀이되는 인트로·CTA 등)가 내레이션 글자 수에서 차지하는 비율. judge.md 위험2 #5: 10% 넘으면 경고 / 차단 [추정]
    "fixed_phrase_ratio": Rule(0.10, 0.25),
    # 장면 kind 순서의 difflib 유사도. 골격만 같은 것은 정책상 허용(인트로·아웃트로 공유 허용) → 단독 차단 없음 [정책·추정]
    "structure_sim": Rule(0.90, None),
    # 같은 상대와 동시에 걸린 신호 수(골격 ≥0.9, 본문 ≥ script_sim 경고선, 시각 또는 제목 경고선). facts 7.4 C1 [정책·추정]
    "template_combo": Rule(2, 3),
    # ── 형식 순환(쇼츠). 대표 형식 = 틀 장면(title·chapter·cta·outro)을 뺀 장면 kind 중 문장 수 가중 최다
    # judge.md 위험2 #7 "같은 형식 연속 금지, 형식별 주 3편 이하" → 2편 연속 경고·3편 연속 차단, 주 4번째 경고·5번째부터 차단 [추정]
    "format_run": {"short": Rule(2, 3)},
    "format_count": {"short": Rule(4, 5)},
    # ── 메타데이터
    # 제목 문자 2-gram Jaccard(숫자→0). [실험] "4인 가족"→"1인 가구"만 바꿈 0.75, 같은 문형 다른 주제 0.24
    "title_sim": Rule(0.45, 0.65),
    # 직전 5편(같은 종류) 중 제목 앞 3어절(숫자→0)이 같은 편 수. judge.md #9 "겹치면 금지"
    "title_prefix": Rule(None, 1),
    # 연번 제목(#123, 제3탄, EP.2 …). judge.md #9 "연번 제목 금지"
    "serial_title": Rule(None, 1),
    # 설명 본문(링크·해시태그·여러 자산에 반복되는 줄 제거) 문자 5-gram Jaccard. facts M3 [추정]
    "desc_sim": Rule(0.50, 0.80),
    # 태그 집합 Jaccard(둘 다 3개 이상일 때). facts M4 [추정]
    "tag_sim": Rule(0.80, None),
    # 해시태그 세트(2개 이상)가 완전히 같은 자산 수. judge.md #9 "해시태그 세트가 같으면 금지"
    "hashtag_same": Rule(None, 1),
    # ── 시각(render 단계)
    # 썸네일·커버 256비트 pHash 해밍거리 min(원본, 색 반전, 윤곽선). 작을수록 비슷함.
    # [실험] 이 저장소 thumbnail 모듈: 같은 글+느낌표만 추가 8~26, 같은 틀·다른 글 46~134, 다른 틀 94~156
    "thumb_dist": Rule(40, 28, "le"),
    # 이 영상의 고유 프레임 중 다른 자산 하나의 프레임과 256비트 해밍 ≤40 으로 일치하는 비율(vPDQ 방식). facts V2a [검증·추정]
    # [실험] 재인코딩 0~6, 자막 띠만 다름 16~32, 같은 틀·다른 내용 62~98
    "frame_reuse": Rule(0.25, 0.50),
    # 프레임 차이로 본 정지 구간(2초 이상) 비율. 이 렌더러는 문장마다 화면이 바뀌고 정지 구간엔 진행 막대만 움직인다.
    # [실험] 실제 렌더 쇼츠 2편 0.60~0.78 → 문장 단위 전개는 통과, 장면 하나를 오래 세워 두는 슬라이드쇼만 걸리게 [추정]
    # (facts 의 S 0.20/0.40 은 촬영 영상 기준이라 이 렌더러에 그대로 쓰면 모든 영상이 걸린다)
    "freeze_ratio": {"short": Rule(0.85, 0.95), "longform": Rule(0.88, 0.96), "*": OFF},
    # 최장 정지 구간(초). [실험] 실제 렌더 쇼츠 최장 4~5초. facts V4b(S 4/8, L 15/45)를 문장 길이만큼 늘림 [추정]
    "longest_freeze": {"short": Rule(8, 15), "longform": Rule(20, 45), "*": OFF},
    # ── 사람 개입·고지·블로그
    # 롱폼 voice 장면 수(원고). 0 이면 경고, require_voice: true 면 차단. judge.md #8
    "voice_scenes": {"longform": Rule(0, None, "le"), "*": OFF},
    # 롱폼 사람 녹음 길이(초, render). judge.md #8 "롱폼은 매주 새 녹음 2분 이상"
    "voice_seconds": {"longform": Rule(120, None, "lt"), "*": OFF},
    # 이번 주 voice 장면이 있는 쇼츠 편수. judge.md #8 "쇼츠 주 3편 이상 운영자 육성" — 모자라면 육성 없는 쇼츠에 경고
    "voice_week": {"short": Rule(3, None, "lt"), "*": OFF},
    # 같은 녹음 파일(내용 해시)을 과거·묶음에서 다시 쓴 횟수. judge.md #8 "녹음 파일 해시가 재사용되면 차단"
    "voice_reuse": Rule(None, 1),
    # AI 사용 고지 누락(1=누락). 합성 음성을 쓰는데 synthetic_media=false, 카드뉴스는 synthetic_media 인데 캡션 고지 없음 [정책]
    "ai_disclosure": Rule(1, None),
    # 블로그 표·도식 수. judge.md #11 "고유 표·도식이 없으면 차단"
    "blog_visuals": {"blog": Rule(None, 0, "le"), "*": OFF},
    # render 단계인데 렌더 결과가 없음 — 검사를 건너뛴 채 발행하지 않게 차단(facts 7.4 종합 6)
    "render_missing": Rule(None, 1),
}

LABELS: dict[str, str] = {
    "script_sim": "대본 본문 유사도",
    "script_sim_num": "숫자 정규화 후 대본 유사도",
    "script_tfidf": "대본 TF-IDF 코사인",
    "sentence_repeat": "자산 내부 같은 문장 반복률",
    "sentence_reuse": "다른 자산과 같은 문장 비율",
    "fixed_phrase_ratio": "고정 문구 비율",
    "structure_sim": "구조 골격 유사도",
    "template_combo": "템플릿 대량생산 결합 신호",
    "format_run": "같은 형식 연속 편수",
    "format_count": "형식별 주간 순번",
    "title_sim": "제목 유사도",
    "title_prefix": "제목 앞 3어절 반복",
    "serial_title": "연번 제목",
    "desc_sim": "설명 유사도",
    "tag_sim": "태그 유사도",
    "hashtag_same": "해시태그 세트 동일",
    "thumb_dist": "썸네일 pHash 거리",
    "frame_reuse": "화면 소재 재사용률",
    "freeze_ratio": "정지 화면 비율",
    "longest_freeze": "최장 정지 구간(초)",
    "voice_scenes": "사람 녹음 장면 수",
    "voice_seconds": "사람 녹음 길이(초)",
    "voice_week": "이번 주 육성 쇼츠 편수",
    "voice_reuse": "녹음 파일 재사용",
    "ai_disclosure": "AI 사용 고지 누락",
    "blog_visuals": "블로그 표·도식 수",
    "render_missing": "렌더 결과 없음",
}

# 지표가 아닌 검사 옵션(기본값). cfg.originality 의 같은 키로 덮어쓴다.
OPTIONS: dict[str, Any] = {
    "boilerplate": [],                 # 허용 상용구 문장(고정 문구로 보고 본문 비교에서 뺀다 — 고정 문구 비율에는 들어간다)
    "fixed_min_assets": 3,             # 고정 문구 후보 자리(틀 장면 문장·첫·끝 문장)에 이 수 이상의 자산이 쓴 문장 = 고정 문구
    "require_voice": False,            # True: 사람 녹음 요건을 차단으로
    "title_prefix_words": 3,
    "title_prefix_window": 5,          # 직전 몇 편과 비교할지
    "recent_weeks": 26,                # 제목·설명·태그·썸네일을 비교할 과거 범위(약 180일)
    "hashtag_min_set": 2,              # 이보다 작은 해시태그 세트는 '세트 동일' 판정에서 뺀다
    "frame_match_bits": 40,            # 256비트 pHash 일치 기준(해밍거리 이하)
    "still_diff": 0.5,                 # 0~255 회색조 평균 절대차가 이보다 작으면 정지로 본다 [실험: 진행 막대만 움직임 0.01~0.1, 전환 2~45]
    "min_freeze_seconds": 2.0,         # 정지 비율에 넣을 최소 정지 길이(ffmpeg freezedetect 기본 d=2 와 같게)
    "longform_max_frames": 120,        # 롱폼 프레임 pHash 표본 상한
    "archive_max_frames": 60,          # 아카이브에 남길 프레임 pHash 상한
    "body_exclude_kinds": ["title", "chapter", "cta", "outro", "cover"],  # 본문 유사도·대표 형식에서 뺄 틀 장면(카드뉴스는 cover·cta)
    "frame_exclude_kinds": ["cta", "outro"],                     # 화면 재사용에서 뺄 장면(브랜드 끝 화면)
    "format_families": {},             # 형식 묶기 {"flow": "steps", "timeline": "steps"} — 비우면 kind 그대로
    "disclosure_words": ["AI", "인공지능", "합성", "TTS", "생성형"],
    "jobs": 2,                         # 영상 분석 동시 실행 수
}

KINDS = ("longform", "short", "cardnews", "blog")


class Thresholds:
    """기본값 + cfg.originality 덮어쓰기."""

    def __init__(self, overrides: dict[str, Any] | None = None):
        self.overrides = dict(overrides or {})
        self.unknown = [k for k in self.overrides if k not in DEFAULTS and k not in OPTIONS and k != "disable"]
        self.disabled = set(self.overrides.get("disable") or [])

    def opt(self, name: str) -> Any:
        return self.overrides.get(name, OPTIONS[name])

    def rule(self, metric: str, kind: str) -> Rule:
        base = DEFAULTS[metric]
        rule = (base.get(kind) or base.get("*") or OFF) if isinstance(base, dict) else base
        if metric in self.disabled:
            return OFF
        if metric == "voice_scenes" and self.opt("require_voice") and rule.warn_at is not None:
            rule = Rule(None, rule.warn_at, rule.cmp)
        if metric in ("voice_seconds", "voice_week") and self.opt("require_voice") and rule.warn_at is not None:
            rule = Rule(None, rule.warn_at, rule.cmp)
        if metric in self.overrides:
            rule = _apply(rule, self.overrides[metric], kind)
        return rule


def _apply(rule: Rule, ov: Any, kind: str) -> Rule:
    if ov is None or ov is False or (isinstance(ov, str) and ov.lower() == "off"):
        return OFF
    if isinstance(ov, (list, tuple)) and len(ov) == 2:
        return replace(rule, warn_at=_f(ov[0]), block_at=_f(ov[1]))
    if isinstance(ov, dict):
        if any(k in ov for k in KINDS) or "*" in ov:
            sub = ov.get(kind, ov.get("*"))
            return rule if sub is None and kind not in ov else _apply(rule, sub, kind)
        out = rule
        if "warn_at" in ov:
            out = replace(out, warn_at=_f(ov["warn_at"]))
        if "block_at" in ov:
            out = replace(out, block_at=_f(ov["block_at"]))
        return out
    raise ValueError(f"originality 설정 형식을 알 수 없습니다: {ov!r} — {{warn_at: x, block_at: y}} 또는 [x, y] 로 적어 주세요.")


def _f(v: Any) -> float | None:
    return None if v is None else float(v)
