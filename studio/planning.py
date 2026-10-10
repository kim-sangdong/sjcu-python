"""주간 계획 뼈대 만들기, 원고 검증, 진행 현황.

  python -m studio plan init [--week 2026-W42]
  python -m studio plan validate [--week ...]
  python -m studio plan status [--week ...]
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

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


def build_brief(week: str) -> str:
    """지난주 보고서와 과거 발행 목록을 모아 이번 주 기획의 입력으로 쓴다."""
    prev = paths.previous_week(week)
    parts = [f"# {week} 기획 입력 자료\n"]
    for name in ("ad-strategy.md", "channel-revenue.md", "ad-decision.md"):
        p = paths.reports_dir(prev) / name
        if p.exists():
            parts.append(f"## 지난주({prev}) {name}\n\n{p.read_text(encoding='utf-8').strip()}\n")
    idx = read_json(paths.archive_index(), default={}) or {}
    titles = [f"- {a.get('asset_id')}: {a.get('title', '')}" for a in idx.get("assets", [])][-120:]
    if titles:
        parts.append("## 이미 발행한 콘텐츠(최근 120개) — 주제·제목·구성이 겹치지 않게\n\n" + "\n".join(titles) + "\n")
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


def _texts_of(obj) -> list[tuple[str, str]]:
    """규칙 검사 대상 텍스트(위치, 내용)."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, VideoScript):
        out += [("title", obj.title), ("description", obj.description), ("pinned_comment", obj.pinned_comment), ("cta", obj.cta)]
        out += [(f"tags", " ".join(obj.tags))]
        if obj.thumbnail:
            out += [("thumbnail", f"{obj.thumbnail.headline} {obj.thumbnail.subline}")]
        for s in obj.scenes():
            out += [(f"scene {s.id}", line) for line in s.narration]
            out += [(f"scene {s.id} params", str(v)) for v in s.params.values() if isinstance(v, str)]
    elif isinstance(obj, CardNews):
        out += [("title", obj.title), ("caption", obj.caption)]
        out += [(f"slide {i + 1}", f"{s.title} {s.body}") for i, s in enumerate(obj.slides)]
    elif isinstance(obj, BlogPost):
        out += [("title", obj.title), ("meta_description", obj.meta_description), ("body", obj.body_markdown)]
    return out


def check_rules(obj, cfg: Config, f: Findings, label: str) -> None:
    for where, text in _texts_of(obj):
        for phrase in cfg.rules.banned_phrases:
            if phrase and phrase in text:
                f.errors.append(f"{label} [{where}] 금지 표현 '{phrase}'")
        for phrase in cfg.rules.caution_phrases:
            if phrase and phrase in text:
                f.warnings.append(f"{label} [{where}] 주의 표현 '{phrase}' — 맥락 확인")


def estimate_seconds(script: VideoScript, cfg: Config) -> float:
    chars = sum(len(re.sub(r"\s", "", line)) for line in script.narration_sentences())
    rate = max(cfg.tts.speaking_rate, 0.5)
    holds = sum(s.hold_seconds for s in script.scenes())
    return chars / CHARS_PER_SECOND / rate + holds + 0.4 * len(script.scenes())


def validate_week(week: str, cfg: Config) -> Findings:
    f = Findings()
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
        if isinstance(obj, VideoScript):
            if obj.format != item.kind:
                f.errors.append(f"{item.id}: format 은 '{item.kind}' 여야 합니다.")
            secs = estimate_seconds(obj, cfg)
            if item.kind == "short" and secs > cfg.video.short_max_seconds:
                f.errors.append(f"{item.id}: 예상 길이 {secs:.0f}초 > 쇼츠 상한 {cfg.video.short_max_seconds}초")
            if item.kind == "longform":
                target = cfg.video.longform_target_minutes * 60
                if not (0.75 * target <= secs <= 1.2 * target):
                    f.warnings.append(f"{item.id}: 예상 길이 {secs / 60:.1f}분 (목표 {cfg.video.longform_target_minutes}분)")
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
