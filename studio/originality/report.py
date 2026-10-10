"""사람(과 원고를 고칠 Claude)이 읽는 originality.md."""

from __future__ import annotations

from typing import TYPE_CHECKING

from studio.models import AssetOriginality, CheckResult, OriginalityReport
from studio.originality.thresholds import LABELS

if TYPE_CHECKING:
    from studio.originality.checker import Checker

KO = {"pass": "통과", "warn": "경고", "block": "차단"}
SEV = {"pass": 0, "warn": 1, "block": 2}
STAGE_KO = {"script": "원고(script) 단계 — 텍스트만", "render": "렌더(render) 단계 — 텍스트 + 썸네일·화면·정지"}


def _val(c: CheckResult) -> str:
    v = c.value
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.2f}"


def _limits(c: CheckResult, ck: "Checker", kind: str) -> str:
    try:
        return ck.th.rule(c.metric, kind).describe()
    except KeyError:
        return ""


def _flagged(a: AssetOriginality) -> list[CheckResult]:
    return sorted((c for c in a.checks if c.verdict != "pass"), key=lambda c: -SEV[c.verdict])


def render_markdown(report: OriginalityReport, ck: "Checker", elapsed: float, *, only: list[str] | None = None) -> str:
    kinds = {d.asset_id: d.kind for d in ck.docs}
    counts = {k: sum(1 for a in report.assets if a.verdict == k) for k in ("block", "warn", "pass")}
    n_recent = sum(1 for p in ck.past if p.recent)
    out = [
        f"# 독창성 검사 — {report.week}",
        "",
        f"- 단계: {STAGE_KO[ck.stage]}" + (f" · 이번 실행 대상: {', '.join(only)}" if only else ""),
        f"- 판정: **{KO[report.verdict]}** (차단 {counts['block']} · 경고 {counts['warn']} · 통과 {counts['pass']})",
        f"- 비교: 이번 주 {len(ck.docs)}개, 과거 아카이브 {len(ck.past)}개(최근 {ck.th.opt('recent_weeks')}주 {n_recent}개) · 고정 문구 {len(ck.fixed)}종",
        f"- 생성: {report.generated_at:%Y-%m-%d %H:%M} KST · {elapsed:.1f}초",
    ]
    if ck.th.unknown:
        out.append(f"- [설정 확인] channel.yaml originality 의 알 수 없는 키: {', '.join(ck.th.unknown)}")
    out += ["", "차단(block)은 발행하지 않는다. 아래 '고칠 것'을 그대로 원고에 반영한 뒤 같은 명령으로 다시 검사한다. "
                "경고(warn)는 발행하되 다음 주 기획에 반영한다.", ""]

    out += ["| 자산 | 종류 | 판정 | 걸린 지표 |", "|---|---|---|---|"]
    for a in sorted(report.assets, key=lambda a: (-SEV[a.verdict], a.asset_id)):
        hits = ", ".join(f"{c.metric} {_val(c)}" + (f"({ck.label(c.compared_with)})" if c.compared_with else "") for c in _flagged(a)[:4])
        out.append(f"| {a.asset_id} | {kinds.get(a.asset_id, '')} | {KO[a.verdict]} | {hits or '-'} |")
    out.append("")

    todo = [a for a in report.assets if a.verdict != "pass"]
    if todo:
        out += ["## 고칠 것", ""]
    for a in sorted(todo, key=lambda a: (-SEV[a.verdict], a.asset_id)):
        kind = kinds.get(a.asset_id, "")
        out += [f"### {a.asset_id} ({kind}) — {KO[a.verdict]}", ""]
        for f in a.fixes:
            out.append(f"- {f}")
        out += ["", "<details><summary>걸린 지표</summary>", ""]
        for c in _flagged(a):
            vs = f" · 상대 {c.compared_with}" if c.compared_with else ""
            out.append(f"- {KO[c.verdict]} `{c.metric}` {LABELS.get(c.metric, '')} = {_val(c)} ({_limits(c, ck, kind)}){vs}"
                       + (f" — {c.detail}" if c.detail else ""))
        out += ["", "</details>", ""]

    out += ["## 이번 주 형식 배치(쇼츠)", ""]
    shorts = [d for d in ck.docs if d.kind == "short"]
    if shorts:
        out.append(" → ".join(f"{d.label}:{d.format}" for d in shorts))
        dist: dict[str, int] = {}
        for d in shorts:
            dist[d.format or "?"] = dist.get(d.format or "?", 0) + 1
        out += ["", "형식 분포: " + ", ".join(f"{k} {v}편" for k, v in sorted(dist.items(), key=lambda kv: -kv[1]))]
        voice = sum(1 for d in shorts if d.voice_scenes > 0)
        out.append(f"육성(voice) 쇼츠: {voice}편")
    else:
        out.append("(쇼츠 없음)")
    out += ["", "## 지표 설명", "",
            "값과 기준은 originality.json 의 checks 에 모두 있다(통과 포함 — 임계값 보정용). 임계값 근거는 studio/originality/thresholds.py.", ""]
    return "\n".join(out) + "\n"
