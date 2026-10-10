"""주간 계획 뼈대 만들기, 원고 검증, 진행 현황.

  python -m studio plan init [--week 2026-W42]
  python -m studio plan validate [--week ...]
  python -m studio plan status [--week ...]
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from studio import paths, schedule
from studio.config import Config, load_config
from studio.models import (
    BlogPost,
    CardNews,
    OriginalityReport,
    PlanItem,
    PublishQueue,
    RenderManifest,
    VideoScript,
    WeekPlan,
    read_json,
    read_model,
    write_model,
)

KIND_DIR = {"longform": "longform", "short": "shorts", "cardnews": "cardnews", "blog": "blog"}
KIND_MODEL = {"longform": VideoScript, "short": VideoScript, "cardnews": CardNews, "blog": BlogPost}

# 한국어 내레이션 속도 추정(공백 제외 글자/초, 말하기 속도 1.0 기준). 렌더 후 실측값이 우선이다.
CHARS_PER_SECOND = 5.5
LONGFORM_TOLERANCE = 0.15   # 롱폼 길이 허용 범위(목표 ±15%) — .claude/skills/longform/SKILL.md 3번과 같다

# 정답이 있는 장면(문제은행 문항에서 가져와 params.bank_id 를 적는 장면 — CONTENT_GUIDE 5장)
BANK_SCENE_KINDS = {"ox", "quiz", "table_blank", "calc", "sequence"}


def asset_path(week: str, kind: str, asset_id: str) -> Path:
    return paths.content_dir(week) / KIND_DIR[kind] / f"{asset_id}.json"


def plan_path(week: str) -> Path:
    return paths.content_dir(week) / "plan.json"


def load_plan(week: str) -> WeekPlan:
    return read_model(plan_path(week), WeekPlan)


# ---------------------------------------------------------------- init

def skeleton(week: str, cfg: Config) -> WeekPlan:
    items: list[PlanItem] = [
        PlanItem(id=paths.longform_id(week), kind="longform", topic="", publish_at=schedule.longform_slot(week, cfg))
    ]
    items += [
        PlanItem(id=paths.short_id(week, i + 1), kind="short", topic="", publish_at=t)
        for i, t in enumerate(schedule.shorts_slots(week, cfg))
    ]
    items += [
        PlanItem(id=paths.cardnews_id(week, i + 1), kind="cardnews", topic="", publish_at=t)
        for i, t in enumerate(schedule.cardnews_slots(week, cfg))
    ]
    items += [
        PlanItem(id=paths.blog_id(week, i + 1), kind="blog", topic="", publish_at=t)
        for i, t in enumerate(schedule.blog_slots(week, cfg))
    ]
    return WeekPlan(week=week, theme="", items=items)


KIND_LABEL = {"longform": "롱폼", "short": "쇼츠", "cardnews": "카드뉴스", "blog": "블로그"}
NEXT_WEEK_HEADING = "다음 주에 반영할 점"


def _section(md: str, heading: str) -> str | None:
    """마크다운에서 '## <heading>' 절 본문(다음 같은 수준 제목 전까지)."""
    m = re.search(rf"^##\s*{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", md, flags=re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else None


def build_brief(week: str) -> str:
    """지난주 보고서·지난주 실행 보고서의 '다음 주에 반영할 점'·과거 발행 목록(형식 포함)을 모아 이번 주 기획의 입력으로 쓴다."""
    prev = paths.previous_week(week)
    parts = [f"# {week} 기획 입력 자료\n"]
    for name in ("ad-strategy.md", "channel-revenue.md", "ad-decision.md"):
        p = paths.reports_dir(prev) / name
        if p.exists():
            parts.append(f"## 지난주({prev}) {name}\n\n{p.read_text(encoding='utf-8').strip()}\n")
    # /weekly-publish 9단계가 실행 보고서에 적은 '다음 주에 반영할 점'을 다음 주 기획이 실제로 읽게 한다
    run_report = paths.content_dir(prev) / "RUN_REPORT.md"
    if run_report.exists():
        md = run_report.read_text(encoding="utf-8")
        nxt = _section(md, NEXT_WEEK_HEADING)
        if nxt:
            parts.append(f"## 지난주({prev}) 실행 보고서의 {NEXT_WEEK_HEADING}\n\n{nxt}\n")
    idx = read_json(paths.archive_index(), default={}) or {}
    assets = [a for a in idx.get("assets", []) if a.get("week", "") < week][-120:]

    def line(a: dict) -> str:
        kind = KIND_LABEL.get(a.get("kind", ""), a.get("kind", ""))
        fmt = f", 형식 {a['format']}" if a.get("format") else ""
        return f"- {a.get('asset_id')}: {a.get('title', '')} ({kind}{fmt})"

    if assets:
        parts.append("## 이미 발행한 콘텐츠(최근 120개) — 주제·제목·구성이 겹치지 않게\n\n" + "\n".join(line(a) for a in assets) + "\n")
        prev_shorts = sorted((a for a in assets if a.get("kind") == "short" and a.get("week") == prev), key=lambda a: a.get("asset_id", ""))[-3:]
        if prev_shorts:
            seq = " → ".join(f"{a.get('format') or '?'}({a.get('asset_id')})" for a in prev_shorts)
            parts.append("## 지난주 마지막 쇼츠 형식(주를 넘어 연속으로 센다)\n\n"
                         f"{seq}\n\n이번 주 첫 쇼츠가 같은 형식이면 독창성 검사의 '같은 형식 연속'에 이어서 셉니다"
                         "(형식 = 틀 장면을 뺀 장면 kind 중 화면 시간 비중이 가장 큰 것).\n")
    else:
        parts.append("## 이미 발행한 콘텐츠\n\n(아직 없음 — 첫 주)\n")
    return "\n".join(parts)


def cmd_init(args: argparse.Namespace) -> int:
    cfg = load_config()
    week = args.week or paths.publishing_week()
    d = paths.content_dir(week)
    for sub in KIND_DIR.values():
        (d / sub).mkdir(parents=True, exist_ok=True)
    if plan_path(week).exists() and not args.force:
        print(f"이미 계획이 있습니다: {plan_path(week)} (덮어쓰려면 --force)")
    else:
        write_model(plan_path(week), skeleton(week, cfg))
        print(f"계획 뼈대 생성: {plan_path(week)}")
    (d / "brief.md").write_text(build_brief(week), encoding="utf-8")
    print(f"기획 입력 자료: {d / 'brief.md'}")
    print(week)
    return 0


# ---------------------------------------------------------------- validate

@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _strings(v: Any, where: str) -> list[tuple[str, str]]:
    """params 안의 글을 모두(목록·사전 안쪽까지) — 화면에 그려지는 표 칸·관계도 이름표·개정 전후 조문도 금지 표현 검사 대상이다."""
    if isinstance(v, str):
        return [(where, v)]
    if isinstance(v, dict):
        return [x for k, sub in v.items() for x in _strings(sub, f"{where}.{k}")]
    if isinstance(v, (list, tuple)):
        return [x for i, sub in enumerate(v) for x in _strings(sub, f"{where}[{i}]")]
    return []


def _texts_of(obj) -> list[tuple[str, str]]:
    """규칙 검사 대상 텍스트(위치, 내용). 발행되거나 화면에 그려지는 글은 모두 넣는다(notes 같은 제작 메모만 뺀다)."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, VideoScript):
        out += [("title", obj.title), ("description", obj.description), ("pinned_comment", obj.pinned_comment), ("cta", obj.cta)]
        out += [("tags", " ".join(obj.tags)), ("hashtags", " ".join(obj.hashtags))]
        if obj.thumbnail:
            out += [("thumbnail", f"{obj.thumbnail.headline} {obj.thumbnail.subline}")]
            out += _strings(obj.thumbnail.params, "thumbnail.params")
        for c in obj.chapters:
            out.append(("chapter", c.title))
        for s in obj.scenes():
            out += [(f"scene {s.id}", line) for line in s.narration]
            out += _strings(s.params, f"scene {s.id} params")
    elif isinstance(obj, CardNews):
        out += [("title", obj.title), ("caption", obj.caption), ("hashtags", " ".join(obj.hashtags))]
        for i, s in enumerate(obj.slides):
            out.append((f"slide {i + 1}", f"{s.title} {s.body}"))
            out += _strings(s.params, f"slide {i + 1} params")
    elif isinstance(obj, BlogPost):
        out += [("title", obj.title), ("keyword", obj.keyword), ("tags", " ".join(obj.tags)),
                ("meta_description", obj.meta_description), ("body", obj.body_markdown)]
    return out


def check_rules(obj, cfg: Config, f: Findings, label: str) -> None:
    for where, text in _texts_of(obj):
        for phrase in cfg.rules.banned_phrases:
            if phrase and phrase in text:
                f.errors.append(f"{label} [{where}] 금지 표현 '{phrase}'")
        for phrase in cfg.rules.caution_phrases:
            if phrase and phrase in text:
                f.warnings.append(f"{label} [{where}] 주의 표현 '{phrase}' — 맥락 확인")


def _num(v: Any, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def scene_extra_seconds(sc) -> float:
    """내레이션·hold_seconds 밖에서 렌더러가 더하는 생각할 시간(타이머·카운트다운·빈칸 think).

    렌더러 기본값과 같게 맞춘다(studio/video/scenes: ox timer 3초, quiz·sequence 카운트다운 = countdown → hold_seconds → 5초,
    flow hidden = countdown → hold_seconds → 3초, table_blank = think × 빈칸 수). hold_seconds 를 카운트다운으로 쓰는 장면은
    hold_seconds 가 이미 더해지므로 두 번 세지 않는다.
    """
    p = sc.params or {}
    if sc.kind == "ox":
        return _num(p.get("timer"), 3.0)
    if sc.kind == "table_blank":
        return _num(p.get("think"), 0.0) * len(p.get("blanks") or [])
    if sc.kind in ("quiz", "sequence") or (sc.kind == "flow" and p.get("hidden") not in (None, [], "")):
        cd = p.get("countdown")
        if isinstance(cd, (int, float)) and not isinstance(cd, bool) and cd > 0:
            return float(cd)
        if sc.hold_seconds > 0:
            return 0.0
        return 3.0 if sc.kind == "flow" else 5.0
    return 0.0


def estimate_seconds(script: VideoScript, cfg: Config) -> float:
    chars = sum(len(re.sub(r"\s", "", line)) for line in script.narration_sentences())
    rate = max(cfg.tts.speaking_rate, 0.5)
    holds = sum(s.hold_seconds + scene_extra_seconds(s) for s in script.scenes())
    return chars / CHARS_PER_SECOND / rate + holds + 0.4 * len(script.scenes())


# ---------------------------------------------------------------- 근거 배지(params.basis)

def check_basis(obj, f: Findings, label: str) -> None:
    """화면 근거 배지에 시행일·선고일이 없으면 오류(CONTENT_GUIDE 7장 1번 — 시행일 없는 근거는 쓰지 않는다).

    렌더러도 같은 검사를 하지만 영상은 경고로만 남기고(timeline.json) 파이프라인이 읽지 않아, 날짜 없는 배지가
    그대로 발행될 수 있었다(리허설 2026-W43). 원고 검증 단계에서 막는다.
    """
    from studio.video.scenes.exam import basis_problems

    spots: list[tuple[str, Any]] = []
    if isinstance(obj, VideoScript):
        spots = [(f"scene {sc.id}", sc.params.get("basis")) for sc in obj.scenes() if sc.params.get("basis")]
    elif isinstance(obj, CardNews):
        spots = [(f"slide {i}", sl.params.get("basis")) for i, sl in enumerate(obj.slides, 1) if sl.params.get("basis")]
    for where, raw in spots:
        for msg in basis_problems(raw):
            f.errors.append(f"{label} [{where}] {msg}")


# ---------------------------------------------------------------- 문제은행 참조(params.bank_id)

def rehearsal() -> bool:
    return os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def bank_refs(obj) -> list[tuple[str, str, dict, Any]]:
    """원고 안의 문제은행 참조: (위치, 장면·슬라이드 kind, params, bank_id 원값). bank_id 가 없는 정답 장면도 넣는다(원값 None)."""
    out: list[tuple[str, str, dict, Any]] = []
    if isinstance(obj, VideoScript):
        for sc in obj.scenes():
            quizlike = sc.kind in BANK_SCENE_KINDS or (sc.kind == "flow" and sc.params.get("hidden") not in (None, [], ""))
            if "bank_id" in sc.params or quizlike:
                out.append((f"scene {sc.id}", sc.kind, sc.params, sc.params.get("bank_id")))
    elif isinstance(obj, CardNews):
        for i, sl in enumerate(obj.slides, 1):
            if "bank_id" in sl.params or sl.kind == "quiz":
                out.append((f"slide {i}", sl.kind, sl.params, sl.params.get("bank_id")))
    return out


def bank_ids(raw: Any) -> list[str] | None:
    """params.bank_id 원값 → id 목록. 문자열 하나 또는 문자열 목록(빈칸표처럼 여러 문항)만 받는다. 형식이 틀리면 None."""
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list) and raw and all(isinstance(x, str) and x.strip() for x in raw):
        return [x.strip() for x in raw]
    return None


def _same_number(a: Any, b: Any) -> bool:
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(x - y) <= 1e-6 * max(1.0, abs(x), abs(y))


def _answer_mismatch(kind: str, params: dict, item) -> str | None:
    """장면 정답과 문제은행 정답이 다르면 설명(정답이 뒤집힌 채 발행되는 것을 막는다)."""
    a = params.get("answer")
    if kind == "ox" and item.type == "ox":
        if isinstance(a, str) and a.strip().upper() in ("O", "X"):
            a = a.strip().upper() == "O"
        if isinstance(a, bool) and a != item.answer:
            return f"장면 answer={'O' if a else 'X'} 인데 문항 정답은 {'O' if item.answer else 'X'}"
    elif kind == "quiz" and item.type == "ox":                       # 카드뉴스 OX 장
        ox = {"O": True, "o": True, "X": False, "x": False}.get(str(a).strip()) if not isinstance(a, bool) else a
        if ox is not None and ox != item.answer:
            return f"장 answer={a!r} 인데 문항 정답은 {'O' if item.answer else 'X'}"
    elif kind == "quiz" and item.type == "mcq":
        choices = params.get("choices") or []
        if isinstance(a, int) and not isinstance(a, bool) and choices == (item.choices or []) and a != item.answer:
            return f"장면 answer={a}(0부터) 인데 문항 정답은 {item.answer}(0부터)"
    elif kind == "calc" and item.type == "calc":
        if a is not None and not _same_number(a, item.answer):
            return f"장면 answer={a} 인데 문항 정답은 {item.answer}"
    return None


def check_bank_refs(obj, f: Findings, label: str, bank_box: dict) -> None:
    """params.bank_id 검사 — 없는 문항·verified 아닌 문항·정답 불일치.

    verified·교차확인을 통과하지 않은 문항은 오류다. 단 리허설(STUDIO_DRY_RUN=1)에서는 경고로 낮춘다
    (법령 API 키가 없는 환경에서 draft 문항으로 파이프라인을 끝까지 돌려 보려고 — 실행 보고서에 적어야 한다).
    """
    refs = bank_refs(obj)
    if not refs:
        return
    if "bank" not in bank_box:
        from studio.bank.exam import bank_cfg
        from studio.bank.store import BankError, load_bank
        try:
            bank_box["bank"] = load_bank()
        except BankError as e:
            bank_box["bank"] = None
            f.errors.append(f"문제은행을 읽지 못했습니다 — {e}")
        bank_box["required"] = bank_cfg(load_config()).crosscheck_required
    bank = bank_box["bank"]
    if bank is None:
        return
    from studio.verify.runner import crosscheck_ok

    for where, kind, params, raw in refs:
        if raw is None:
            f.warnings.append(f"{label} [{where}] 정답이 있는 장면({kind})에 params.bank_id 가 없습니다 — "
                              "검증된 문제은행 문항에서 가져와 id 를 적으세요(CONTENT_GUIDE 5장)")
            continue
        ids = bank_ids(raw)
        if ids is None:
            f.errors.append(f"{label} [{where}] params.bank_id 는 문항 id 문자열 또는 문자열 목록이어야 합니다: {raw!r}")
            continue
        for qid in ids:
            item = bank.get(qid)
            if item is None:
                f.errors.append(f"{label} [{where}] 문제은행에 없는 문항 {qid}")
                continue
            if item.status != "verified" or not crosscheck_ok(item, bank_box["required"]):
                why = item.status if item.status != "verified" else "교차확인 미완료"
                msg = f"{label} [{where}] 문항 {qid} 는 {why} — 콘텐츠에는 verified·교차확인 통과 문항만 씁니다(`bank list --ready`)"
                if rehearsal():
                    f.warnings.append(msg + " [리허설이라 경고 — 실제 발행 금지, 실행 보고서에 적을 것]")
                else:
                    f.errors.append(msg)
            if len(ids) == 1:
                bad = _answer_mismatch(kind, params, item)
                if bad:
                    f.errors.append(f"{label} [{where}] 정답 불일치: {bad} ({qid})")


def validate_week(week: str, cfg: Config) -> Findings:
    f = Findings()
    bank_box: dict = {}
    try:
        plan = load_plan(week)
    except FileNotFoundError:
        f.errors.append(f"plan.json 이 없습니다. 먼저 `python -m studio plan init --week {week}`")
        return f
    except ValidationError as e:
        f.errors.append(f"plan.json 형식 오류: {e}")
        return f
    if not plan.theme.strip():
        f.errors.append("plan.theme 이 비어 있습니다.")
    for item in plan.items:
        if not item.topic.strip():
            f.errors.append(f"{item.id}: plan 의 topic 이 비어 있습니다.")
        p = asset_path(week, item.kind, item.id)
        if not p.exists():
            f.errors.append(f"{item.id}: 원고 파일이 없습니다 ({p.relative_to(paths.ROOT)})")
            continue
        try:
            obj = read_model(p, KIND_MODEL[item.kind])
        except ValidationError as e:
            f.errors.append(f"{item.id}: 형식 오류 — {e.errors()[0]['loc']} {e.errors()[0]['msg']} (총 {e.error_count()}건)")
            continue
        if obj.id != item.id or obj.week != week:
            f.errors.append(f"{item.id}: 파일 안의 id/week 가 계획과 다릅니다 ({obj.id}, {obj.week})")
        check_rules(obj, cfg, f, item.id)
        check_basis(obj, f, item.id)
        check_bank_refs(obj, f, item.id, bank_box)
        if isinstance(obj, VideoScript):
            if obj.format != item.kind:
                f.errors.append(f"{item.id}: format 은 '{item.kind}' 여야 합니다.")
            secs = estimate_seconds(obj, cfg)
            if item.kind == "short" and secs > cfg.video.short_max_seconds:
                f.errors.append(f"{item.id}: 예상 길이 {secs:.0f}초 > 쇼츠 상한 {cfg.video.short_max_seconds}초")
            if item.kind == "longform":
                target = cfg.video.longform_target_minutes * 60
                lo, hi = (1 - LONGFORM_TOLERANCE) * target, (1 + LONGFORM_TOLERANCE) * target
                if not (lo <= secs <= hi):
                    f.warnings.append(f"{item.id}: 예상 길이 {secs / 60:.1f}분 — 목표 {cfg.video.longform_target_minutes}분 ±15%"
                                      f"({lo / 60:.1f}~{hi / 60:.1f}분) 밖")
            if not obj.thumbnail and item.kind == "longform":
                f.errors.append(f"{item.id}: 롱폼은 thumbnail 이 필요합니다.")
    known = {i.id for i in plan.items}
    for kind, sub in KIND_DIR.items():
        for p in sorted((paths.content_dir(week) / sub).glob("*.json")):
            if p.stem not in known:
                f.warnings.append(f"계획에 없는 파일: {p.relative_to(paths.ROOT)}")
    return f


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config()
    week = args.week or paths.publishing_week()
    f = validate_week(week, cfg)
    for w in f.warnings:
        print(f"[경고] {w}")
    for e in f.errors:
        print(f"[오류] {e}")
    print(f"{week}: 오류 {len(f.errors)}건, 경고 {len(f.warnings)}건")
    return 1 if f.errors else 0


# ---------------------------------------------------------------- status

def cmd_status(args: argparse.Namespace) -> int:
    week = args.week or paths.publishing_week()
    d = paths.content_dir(week)
    try:
        plan = load_plan(week)
    except FileNotFoundError:
        print(f"{week}: 계획 없음")
        return 1
    manifest = read_model(d / "manifest.json", RenderManifest) if (d / "manifest.json").exists() else RenderManifest(week=week)
    report = read_model(d / "originality.json", OriginalityReport) if (d / "originality.json").exists() else None
    queue = read_model(d / "queue.json", PublishQueue) if (d / "queue.json").exists() else PublishQueue(week=week)
    verdicts = {a.asset_id: a.verdict for a in report.assets} if report else {}
    print(f"{week}  주제: {plan.theme}")
    print(f"{'ID':<14} {'종류':<9} {'원고':<4} {'렌더':<4} {'검사':<6} 발행")
    for item in plan.items:
        has_script = "O" if asset_path(week, item.kind, item.id).exists() else "-"
        rendered = "O" if manifest.get(item.id) else "-"
        pubs = ", ".join(f"{t.platform}:{t.status}" for t in queue.targets if t.asset_id == item.id) or "-"
        print(f"{item.id:<14} {item.kind:<9} {has_script:<4} {rendered:<4} {verdicts.get(item.id, '-'):<6} {pubs}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("plan", help="주간 계획·원고 검증·현황")
    s = p.add_subparsers(dest="plan_cmd", metavar="<하위명령>")
    a = s.add_parser("init", help="이번 주 계획 뼈대와 기획 입력 자료(brief.md) 만들기")
    a.add_argument("--week")
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_init)
    v = s.add_parser("validate", help="원고 형식·금지 표현·길이 검사")
    v.add_argument("--week")
    v.set_defaults(func=cmd_validate)
    st = s.add_parser("status", help="주차 진행 현황")
    st.add_argument("--week")
    st.set_defaults(func=cmd_status)
