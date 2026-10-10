"""주간 보고 데이터 — reports/<week>/channel-revenue.md·.csv.

숫자는 전부 이 모듈이 계산한다(보고서 맨 아래에 계산식 각주). Claude Code(/channel-revenue)는 해석만 덧붙인다.
조회 수의 기준:
- 유튜브는 YouTube Analytics 의 기간 합계.
- 인스타·페북·틱톡은 누적값만 주므로 '이번 수집 누적 − 지난주 수집 누적'(증분). 지난주 값이 없으면
  이번 주 게시물은 누적 전체(정확), 그 전 게시물은 누적 전체를 쓰되 '기준 없음'으로 경고(과대 가능).
수집 실패한 원천의 숫자는 0 이 아니라 '결손'으로 표시하고, 맨 위 '데이터 결손 경고'에 반드시 적는다.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from studio import paths
from studio.analytics.attribution import UNATTRIBUTED_LABEL, Attribution, attribute
from studio.analytics.collect import load_metrics
from studio.analytics.common import (
    ALL_SOURCES,
    CONTENT_SOURCES,
    FUNNEL_PLATFORMS,
    KIND_LABEL,
    PLATFORM_LABEL,
    SOURCE_LABEL,
    Period,
    content_week,
    iso_now,
    kind_of,
    parse_time,
    ratio,
    settings,
)
from studio.config import Config, load_config

VIEW_FIELDS = ("views", "reach", "likes", "comments", "shares", "saves", "watch_minutes")
FUNNEL_FIELDS = ("clicks", "installs", "trials", "paid", "renewals", "refunds", "revenue_usd", "revenue_krw", "refund_krw")
USABLE = ("ok", "partial")
TOP_N = 10
STALE_HOURS = 24
PERIOD_MISMATCH = timedelta(hours=6)
KINDS = ("longform", "short", "cardnews", "blog")
PLATFORM_ORDER = (*FUNNEL_PLATFORMS, "other")
CSV_COLUMNS = ("week", "asset_id", "content_week", "kind", "platform", "views", "views_basis", "reach", "likes", "comments",
               "shares", "saves", "watch_minutes", "clicks", "ctr_pct", "installs", "cvr_install_pct", "trials", "paid",
               "renewals", "refunds", "revenue_usd", "revenue_krw", "revenue_per_1k_views_krw")


# ---------------------------------------------------------------- 원천 상태

def source_status(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in ALL_SOURCES:
        res = (metrics.get("sources") or {}).get(s)
        if res is None:
            st = "missing"
        elif res.get("skipped"):
            st = "skipped"
        elif not res.get("ok"):
            st = "failed"
        elif res.get("dry_run"):
            st = "dry_run"
        elif res.get("partial"):
            st = "partial"
        else:
            st = "ok"
        out[s] = {"status": st, "fake": bool(res and res.get("fake")), "error": (res or {}).get("error"),
                  "last_error": (res or {}).get("last_error"), "last_attempt_at": (res or {}).get("last_attempt_at"),
                  "collected_at": (res or {}).get("collected_at"), "warnings": list((res or {}).get("warnings") or []),
                  "period_end": (res or {}).get("period_end")}
    return out


def _usable(status: dict[str, dict[str, Any]], s: str) -> bool:
    return status[s]["status"] in USABLE


# ---------------------------------------------------------------- 조회(콘텐츠 지표)

def _delta(cur: Any, base: Any) -> float | None:
    if cur is None:
        return None
    if base is None:
        return float(cur)
    return max(float(cur) - float(base), 0.0)


def period_rows(metrics: dict[str, Any], baseline: dict[str, Any] | None) -> list[dict[str, Any]]:
    """원천별 콘텐츠 지표 → 기간 값. 누적 원천은 지난주 수집값과의 차이. 각 줄에 basis 를 단다."""
    period = Period.from_json(metrics["period"])
    status = source_status(metrics)
    base_idx: dict[tuple[str, str], dict[str, Any]] = {}
    if baseline:
        bstatus = source_status(baseline)
        for s in CONTENT_SOURCES:
            if bstatus[s]["status"] in USABLE and bstatus[s]["fake"] == status[s]["fake"]:
                for r in baseline["sources"][s].get("rows") or []:
                    base_idx[(r["platform"], r["remote_id"])] = r
    out = []
    for s in CONTENT_SOURCES:
        if not _usable(status, s):
            continue
        for r in metrics["sources"][s].get("rows") or []:
            row = dict(r)
            if r.get("lifetime"):
                b = base_idx.get((r["platform"], r["remote_id"]))
                if b is not None:
                    for f in VIEW_FIELDS:
                        row[f] = _delta(r.get(f), b.get(f))
                    row["basis"] = "delta"
                else:
                    pub = parse_time(r.get("published_at"))
                    row["basis"] = "new" if pub and pub >= period.start else "baseline_missing"
            else:
                row["basis"] = "period"
            out.append(row)
    return out


# ---------------------------------------------------------------- 요약 만들기

def _add(acc: dict[str, Any], key: str, v: Any) -> None:
    if v is None:
        return
    acc[key] = (acc.get(key) or 0) + v


def _derived(r: dict[str, Any]) -> dict[str, Any]:
    r["ctr"] = ratio(r.get("clicks"), r.get("views"))
    r["cvr_install"] = ratio(r.get("installs"), r.get("clicks"))
    rpm = ratio(r.get("revenue_krw"), r.get("views"))
    r["revenue_per_1k_views"] = rpm * 1000 if rpm is not None else None
    return r


def build(week: str, cfg: Config | None = None, *, with_previous: bool = True,
          metrics: dict[str, Any] | None = None, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    st = settings(cfg)
    metrics = metrics or load_metrics(week)
    if not metrics:
        raise FileNotFoundError(f"reports/{week}/metrics.json 이 없습니다. 먼저 `python -m studio analytics collect --week {week}` 를 실행하세요.")
    prev = paths.previous_week(week)
    if baseline is None:
        baseline = load_metrics(prev)
    period = Period.from_json(metrics["period"])
    usd_krw = float((metrics.get("settings") or {}).get("usd_krw") or st.usd_krw)
    window_days = int((metrics.get("settings") or {}).get("click_window_days") or st.window_days)
    status = source_status(metrics)
    funnel_ok = _usable(status, "funnel")

    # 1) 조회
    rows = period_rows(metrics, baseline)
    content: dict[tuple[str, str], dict[str, Any]] = {}
    platform_views: dict[str, dict[str, Any]] = defaultdict(dict)
    basis_count: dict[str, Counter] = defaultdict(Counter)
    yt_revenue: float | None = None
    subs_net: float | None = None
    for r in rows:
        p = r["platform"]
        basis_count[p][r["basis"]] += 1
        for f in VIEW_FIELDS:
            _add(platform_views[p], f, r.get(f))
        if p == "youtube":
            extra = r.get("extra") or {}
            if extra.get("yt_revenue_usd") is not None:
                yt_revenue = (yt_revenue or 0) + extra["yt_revenue_usd"]
            if r.get("subs_gained") is not None:
                subs_net = (subs_net or 0) + r["subs_gained"] - (r.get("subs_lost") or 0)
        if not r.get("asset_id"):
            continue
        c = content.setdefault((r["asset_id"], p), {"asset_id": r["asset_id"], "platform": p})
        for f in VIEW_FIELDS:
            _add(c, f, r.get(f))
        c["views_basis"] = r["basis"] if c.get("views_basis") in (None, r["basis"]) else "mixed"

    # 2) 귀속
    att: Attribution | None = None
    if funnel_ok:
        data = metrics["sources"]["funnel"].get("data") or {}
        att = attribute(data.get("link_clicks") or [], data.get("app_installs") or [], data.get("purchases") or [], period,
                        window_days=window_days, usd_krw=usd_krw, require_click=st.require_click,
                        include_sandbox=st.include_sandbox)
        for (asset, p), f in att.by_content.items():
            c = content.setdefault((asset, p), {"asset_id": asset, "platform": p})
            c.update({k: v for k, v in f.to_dict(usd_krw).items() if k in FUNNEL_FIELDS})

    def view_state(p: str) -> str:
        if p in CONTENT_SOURCES:
            return status[p]["status"]
        return "none"     # 티스토리 등: 조회 수집 원천 없음

    content_rows = []
    for (asset, p), c in sorted(content.items()):
        c.update(kind=kind_of(asset), content_week=content_week(asset), view_state=view_state(p))
        if not funnel_ok:
            for f in FUNNEL_FIELDS:
                c[f] = None
        else:
            for f in FUNNEL_FIELDS:
                c.setdefault(f, 0)
        for f in VIEW_FIELDS:
            c.setdefault(f, None)
        c.setdefault("views_basis", None)
        content_rows.append(_derived(c))

    # 3) 플랫폼 깔때기
    platforms = []
    for p in PLATFORM_ORDER:
        vs = view_state(p) if p != "other" else "none"
        f = att.by_platform.get(p) if att else None
        views = platform_views.get(p, {}).get("views", 0) if vs in USABLE else None   # 수집 성공·게시물 0개면 0
        if p == "other" and not (f and (f.clicks or f.installs or f.paid or f.renewals or f.refunds)):
            continue
        row = {"platform": p, "view_state": vs, "views": views}
        fd = f.to_dict(usd_krw) if f else ({k: 0 for k in FUNNEL_FIELDS} if att else {})
        row.update({k: (fd.get(k) if att else None) for k in FUNNEL_FIELDS})
        platforms.append(_derived(row))

    # 4) 콘텐츠(플랫폼 합산)
    assets: dict[str, dict[str, Any]] = {}
    for c in content_rows:
        a = assets.setdefault(c["asset_id"], {"asset_id": c["asset_id"], "kind": c["kind"], "content_week": c["content_week"],
                                              "platforms": [], "views_known": False})
        a["platforms"].append(c["platform"])
        a["platforms"].sort(key=lambda x: PLATFORM_ORDER.index(x) if x in PLATFORM_ORDER else len(PLATFORM_ORDER))
        if c["views"] is not None:
            a["views_known"] = True
        for f in (*VIEW_FIELDS, *FUNNEL_FIELDS):
            _add(a, f, c.get(f))
    asset_rows = []
    for a in assets.values():
        for f in (*VIEW_FIELDS, *FUNNEL_FIELDS):
            a.setdefault(f, None if f in VIEW_FIELDS or not funnel_ok else 0)
        asset_rows.append(_derived(a))

    def rank_key(a: dict[str, Any]) -> tuple:
        return (a.get("revenue_krw") or 0, a.get("paid") or 0, a.get("installs") or 0, a.get("clicks") or 0, a.get("views") or 0)

    ranked = sorted(asset_rows, key=lambda a: (tuple(-x for x in rank_key(a)), a["asset_id"]))
    top = ranked[:TOP_N]
    top_ids = {a["asset_id"] for a in top}
    bottom = [a for a in sorted(asset_rows, key=lambda a: (rank_key(a), a["asset_id"]))
              if a["views_known"] and a["asset_id"] not in top_ids][:TOP_N]

    # 5) 형식별
    kinds = []
    for k in KINDS:
        group = [a for a in asset_rows if a["kind"] == k]
        row: dict[str, Any] = {"kind": k, "count": len(group)}
        for f in (*VIEW_FIELDS, *FUNNEL_FIELDS):
            vals = [a[f] for a in group if a.get(f) is not None]
            row[f] = sum(vals) if vals else (0 if group and f in FUNNEL_FIELDS and funnel_ok else None)
        viewed = [a for a in group if a["views_known"]]
        row["views_per_content"] = ratio(row["views"], len(viewed) or None)
        _derived(row)
        # CTR·1천 조회당 매출은 조회가 수집된 콘텐츠만으로(조회 없는 지난 콘텐츠의 갱신 매출이 섞이지 않게)
        if funnel_ok and viewed:
            row["ctr"] = ratio(sum(a["clicks"] or 0 for a in viewed), row["views"])
            rpm = ratio(sum(a["revenue_krw"] or 0 for a in viewed), row["views"])
            row["revenue_per_1k_views"] = rpm * 1000 if rpm is not None else None
        kinds.append(row)

    # 6) KPI
    view_missing = [p for p in CONTENT_SOURCES if status[p]["status"] not in (*USABLE, "skipped")]
    known_views = [r["views"] for r in platforms if r["views"] is not None]
    total = att.total.to_dict(usd_krw) if att else None
    kpi: dict[str, Any] = {
        "views": sum(known_views) if known_views else None,
        "subs_net": subs_net,
        **{k: (total[k] if total else None) for k in FUNNEL_FIELDS},
        "unattributed_installs": sum(att.unattributed_installs.values()) if att else None,
        "yt_revenue_usd": round(yt_revenue, 2) if yt_revenue is not None else None,
    }
    kpi["ctr"] = ratio(kpi["clicks"], kpi["views"])
    kpi["cvr_install"] = ratio(kpi["installs"], kpi["clicks"])

    summary: dict[str, Any] = {
        "week": week,
        "period": period.to_json(),
        "generated_at": iso_now(),
        "collected_at": metrics.get("collected_at"),
        "fake": bool(metrics.get("fake")),
        "usd_krw": usd_krw,
        "window_days": window_days,
        "require_click": st.require_click,
        "sources": status,
        "funnel_ok": funnel_ok,
        "view_missing": view_missing,
        "kpi": kpi,
        "platforms": platforms,
        "content": content_rows,
        "assets": asset_rows,
        "top": top,
        "bottom": bottom,
        "kinds": kinds,
        "traffic": (metrics["sources"].get("youtube") or {}).get("traffic_sources") or [] if _usable(status, "youtube") else [],
        "attribution": att.to_dict() if att else None,
        "basis_count": {k: dict(v) for k, v in basis_count.items()},
        "previous": None,
    }
    summary["warnings"], summary["notes"] = data_warnings(summary, metrics, cfg, att, baseline is not None)
    if with_previous and baseline and bool(baseline.get("fake")) != summary["fake"]:
        summary["notes"].append(f"지난주({prev}) 자료는 {'합성' if baseline.get('fake') else '실제'} 데이터라 증감 비교에서 뺐습니다.")
    elif with_previous and baseline:
        try:
            prev_sum = build(prev, cfg, with_previous=False, metrics=baseline)
            summary["previous"] = {"week": prev, "kpi": prev_sum["kpi"], "fake": prev_sum["fake"]}
        except (FileNotFoundError, KeyError, ValueError) as e:
            summary["notes"].append(f"지난주({prev}) 수치를 계산하지 못했습니다: {e}")
    return summary


# ---------------------------------------------------------------- 결손 경고

def data_warnings(s: dict[str, Any], metrics: dict[str, Any], cfg: Config, att: Attribution | None,
                  has_baseline: bool) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    notes: list[str] = []
    week = s["week"]
    if s["fake"]:
        warnings.append("합성(--fake) 데이터입니다. 실제 성과가 아니므로 판단·보고에 쓰지 마세요.")
    for src, info in s["sources"].items():
        label = SOURCE_LABEL.get(src, src)
        stt = info["status"]
        hint = f"`python -m studio analytics collect --week {week} --sources {src}`"
        if stt == "missing":
            warnings.append(f"{label}: 이번 수집에 없습니다 — 이 원천의 숫자는 '결손'(0 이 아니라 모름). {hint}")
        elif stt == "failed":
            warnings.append(f"{label}: 수집 실패 — {info['error']} (다시: {hint})")
        elif stt == "dry_run":
            warnings.append(f"{label}: dry-run 결과라 실제 수집값이 아닙니다.")
        elif stt == "partial":
            first = info["warnings"][0] if info["warnings"] else ""
            more = f" 외 {len(info['warnings']) - 1}건(아래 참고)" if len(info["warnings"]) > 1 else ""
            warnings.append(f"{label}: 일부만 수집됨 — {first}{more}")
        elif stt == "skipped":
            notes.append(f"{label}: 설정에서 꺼져 있어 수집하지 않았습니다.")
        if info.get("last_error"):
            warnings.append(f"{label}: 최근 수집({info['last_attempt_at']})이 실패해 이전 수집본({info['collected_at']})을 그대로 썼습니다 — "
                            f"{info['last_error']}")
        skip_first = stt == "partial"
        for i, w in enumerate(info["warnings"]):
            if not (skip_first and i == 0):
                notes.append(f"{label}: {w}")
    for w in (metrics.get("mapping") or {}).get("warnings") or []:
        warnings.append(f"게시물 매핑: {w}")
    for p, cnt in (s.get("basis_count") or {}).items():
        n = cnt.get("baseline_missing", 0)
        if n:
            warnings.append(f"{PLATFORM_LABEL.get(p, p)}: 지난주 수집값이 없는 이전 게시물 {n}개는 누적 조회를 이번 주 값으로 썼습니다(과대 가능).")
    if att:
        warnings += [f"귀속: {w}" for w in att.warnings]
        installs = s["kpi"].get("installs") or 0
        un = s["kpi"].get("unattributed_installs") or 0
        if installs and un / installs > 0.5:
            notes.append(f"설치 {installs}건 중 {un}건({un / installs:.0%})이 미귀속입니다 — iOS·스토어 검색 유입은 콘텐츠로 이을 수 없습니다.")
        if att.excluded.get("sandbox"):
            notes.append(f"SANDBOX(테스트 결제) 이벤트 {att.excluded['sandbox']}건은 매출에서 뺐습니다(analytics.include_sandbox).")
    if not cfg.app.link_base:
        warnings.append("channel.yaml app.link_base 가 비어 있어 설명란·블로그의 앱 링크가 추적 링크가 아닙니다 — 클릭·콘텐츠 귀속이 잡히지 않습니다.")
    # 원천별 수집 시점 차이
    end = parse_time(s["period"]["end"])
    for src, info in s["sources"].items():
        pe = parse_time(info.get("period_end"))
        if end and pe and abs(end - pe) > PERIOD_MISMATCH and info["status"] in USABLE:
            warnings.append(f"{SOURCE_LABEL.get(src, src)}: 다른 원천과 수집 시점이 다릅니다(기간 끝 {pe:%m-%d %H:%M}) — 원천 사이 비율이 틀어질 수 있습니다.")
    collected = parse_time(s.get("collected_at"))
    if collected and datetime.now(paths.KST) - collected > timedelta(hours=STALE_HOURS) and s["period"].get("partial"):
        notes.append(f"수집한 지 {STALE_HOURS}시간이 넘었습니다({collected:%m-%d %H:%M}). 최신 값이 필요하면 collect 를 다시 실행하세요.")
    if s["period"].get("partial"):
        notes.append("주가 아직 끝나지 않아 기간 끝을 수집 시각으로 잘랐습니다.")
    notes.append("티스토리(블로그) 조회 수는 수집 원천이 없어 비어 있습니다(클릭·설치·결제는 집계).")
    notes.append("유튜브 분석 값은 1~3일 늦게 반영될 수 있습니다(미확인) — 마지막 2~3일은 다음 수집에서 늘어날 수 있습니다.")
    if not has_baseline:
        notes.append(f"지난주({paths.previous_week(week)}) metrics.json 이 없어 지난주 대비 증감을 낼 수 없습니다.")
    return warnings, notes


# ---------------------------------------------------------------- 표시

def f_int(v: Any) -> str:
    return "—" if v is None else f"{round(v):,}"


def f_pct(r: Any, digits: int = 1) -> str:
    return "—" if r is None else f"{r * 100:.{digits}f}%"


def f_usd(v: Any) -> str:
    return "—" if v is None else f"${v:,.2f}"


def change(cur: Any, prev: Any) -> str:
    if cur is None or prev is None:
        return "—"
    if prev == 0:
        return "—" if cur == 0 else "신규"
    return f"{(cur - prev) / prev * 100:+.1f}%"


def change_pp(cur: Any, prev: Any) -> str:
    """비율의 증감(퍼센트포인트)."""
    if cur is None or prev is None:
        return "—"
    return f"{(cur - prev) * 100:+.1f}%p"


def _md_table(head: list[str], rows: list[list[str]], left: int = 1) -> str:
    """앞쪽 left 개 열은 왼쪽, 나머지(숫자)는 오른쪽 정렬."""
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" if i < left else "---:" for i in range(len(head))) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _miss(v: Any, missing: bool, fmt=f_int) -> str:
    return "결손" if missing and v is None else fmt(v)


def render_markdown(s: dict[str, Any]) -> str:
    k, prev = s["kpi"], (s.get("previous") or {}).get("kpi") or {}
    fm = not s["funnel_ok"]
    period = s["period"]
    start = parse_time(period["start"]).astimezone(paths.KST)
    end = parse_time(period["end"]).astimezone(paths.KST)
    out = [f"# 채널 매출 보고 — {s['week']}" + (" [합성 데이터]" if s["fake"] else ""), ""]
    out.append(f"- 기간: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} KST" + (" (진행 중 — 수집 시각까지)" if period.get("partial") else ""))
    out.append(f"- 수집: {s.get('collected_at') or '—'} · 보고 생성: {s['generated_at']}")
    out.append(f"- 환율 가정: 1 USD = {s['usd_krw']:,.0f}원 (channel.yaml analytics.usd_krw) · 귀속 창: 클릭 후 {s['window_days']}일")
    out.append("")
    out.append("## 데이터 결손 경고")
    out.append("")
    out += [f"- {w}" for w in s["warnings"]] or ["- 없음 — 모든 원천을 수집했습니다."]
    out.append("")

    out.append("## 주간 KPI")
    out.append("")
    vm = bool(s["view_missing"])
    views_cell = f_int(k["views"]) + (f" (일부 결손: {', '.join(PLATFORM_LABEL[p] for p in s['view_missing'])})" if vm and k["views"] is not None else "")
    rows = [
        ["조회[^views]", views_cell if k["views"] is not None else "결손", f_int(prev.get("views")), change(k["views"], prev.get("views"))],
        ["클릭[^clicks]", _miss(k["clicks"], fm), f_int(prev.get("clicks")), change(k["clicks"], prev.get("clicks"))],
        ["CTR[^ctr]", _miss(k["ctr"], fm, f_pct), f_pct(prev.get("ctr")), change_pp(k["ctr"], prev.get("ctr"))],
        ["설치[^installs]", _miss(k["installs"], fm), f_int(prev.get("installs")), change(k["installs"], prev.get("installs"))],
        ["설치 전환율[^cvr]", _miss(k["cvr_install"], fm, f_pct), f_pct(prev.get("cvr_install")),
         change_pp(k["cvr_install"], prev.get("cvr_install"))],
        ["체험 시작[^trials]", _miss(k["trials"], fm), f_int(prev.get("trials")), change(k["trials"], prev.get("trials"))],
        ["유료 전환[^paid]", _miss(k["paid"], fm), f_int(prev.get("paid")), change(k["paid"], prev.get("paid"))],
        ["갱신 결제", _miss(k["renewals"], fm), f_int(prev.get("renewals")), change(k["renewals"], prev.get("renewals"))],
        ["매출(원)[^rev]", _miss(k["revenue_krw"], fm), f_int(prev.get("revenue_krw")), change(k["revenue_krw"], prev.get("revenue_krw"))],
        ["매출(USD)", _miss(k["revenue_usd"], fm, f_usd), f_usd(prev.get("revenue_usd")), change(k["revenue_usd"], prev.get("revenue_usd"))],
        ["환불(원)", _miss(k["refund_krw"], fm), f_int(prev.get("refund_krw")), change(k["refund_krw"], prev.get("refund_krw"))],
        ["미귀속 설치[^attr]", _miss(k["unattributed_installs"], fm), f_int(prev.get("unattributed_installs")),
         change(k["unattributed_installs"], prev.get("unattributed_installs"))],
        ["유튜브 구독 순증", f_int(k["subs_net"]), f_int(prev.get("subs_net")), change(k["subs_net"], prev.get("subs_net"))],
    ]
    if k.get("yt_revenue_usd") is not None:
        rows.append(["유튜브 광고 수익(추정)", f_usd(k["yt_revenue_usd"]), f_usd(prev.get("yt_revenue_usd")),
                     change(k["yt_revenue_usd"], prev.get("yt_revenue_usd"))])
    prev_label = f"지난주({s['previous']['week']})" if s.get("previous") else "지난주"
    out.append(_md_table(["지표", "이번 주", prev_label, "증감[^wow]"], rows))
    out.append("")

    out.append("## 플랫폼별 깔때기")
    out.append("")
    prow = []
    for r in s["platforms"]:
        vmiss = r["view_state"] not in USABLE and r["view_state"] not in ("none", "skipped")
        prow.append([PLATFORM_LABEL.get(r["platform"], r["platform"]), _miss(r["views"], vmiss), _miss(r["clicks"], fm),
                     _miss(r["ctr"], fm, f_pct), _miss(r["installs"], fm), _miss(r["cvr_install"], fm, f_pct),
                     _miss(r["trials"], fm), _miss(r["paid"], fm), _miss(r["revenue_krw"], fm),
                     _miss(r["revenue_per_1k_views"], fm)])
    att = s.get("attribution")
    if att:
        u = att["unattributed"]
        prow.append(["미귀속", "—", "—", "—", f_int(u["installs"]), "—", f_int(u["trials"]), f_int(u["paid"]), f_int(u["revenue_krw"]), "—"])
    prow.append(["**합계**", f_int(k["views"]), _miss(k["clicks"], fm), _miss(k["ctr"], fm, f_pct), _miss(k["installs"], fm),
                 _miss(k["cvr_install"], fm, f_pct), _miss(k["trials"], fm), _miss(k["paid"], fm), _miss(k["revenue_krw"], fm),
                 _miss(_rpm(k), fm)])
    out.append(_md_table(["플랫폼", "조회", "클릭", "CTR", "설치", "설치 전환율", "체험", "유료 전환", "매출(원)", "1천 조회당 매출(원)[^rpm]"], prow))
    out.append("")
    if att:
        parts = [f"{UNATTRIBUTED_LABEL[r]} {n}" for r, n in sorted(att["unattributed_installs"].items())]
        ev = [f"{UNATTRIBUTED_LABEL.get(r, r)} {n}" for r, n in sorted(att["unattributed_events"].items())]
        out.append(f"- 미귀속 설치 내역: {', '.join(parts) or '없음'}")
        out.append(f"- 미귀속 결제 이벤트 내역: {', '.join(ev) or '없음'}")
        ex = att.get("excluded") or {}
        if ex:
            names = {"sandbox": "SANDBOX", "test": "TEST", "out_of_period": "기간 밖", "duplicate_event": "중복 이벤트",
                     "duplicate_install": "중복 설치", "unknown_price": "금액 모름(건수만 반영)"}
            out.append("- 매출 계산에서 뺀 이벤트: " + ", ".join(f"{names.get(a, a)} {b}" for a, b in sorted(ex.items())))
        out.append("")

    def asset_table(items: list[dict[str, Any]]) -> str:
        rows_ = []
        for i, a in enumerate(items, 1):
            rows_.append([str(i), a["asset_id"], KIND_LABEL.get(a["kind"] or "", "—"),
                          "/".join(PLATFORM_LABEL.get(p, p) for p in a["platforms"]),
                          f_int(a["views"]), _miss(a["clicks"], fm), _miss(a["ctr"], fm, f_pct), _miss(a["installs"], fm),
                          _miss(a["paid"], fm), _miss(a["revenue_krw"], fm), _miss(a["revenue_per_1k_views"], fm)])
        return _md_table(["순위", "콘텐츠", "형식", "플랫폼", "조회", "클릭", "CTR", "설치", "유료 전환", "매출(원)", "1천 조회당 매출(원)"],
                         rows_, left=4)

    out.append(f"## 콘텐츠별 상위 {TOP_N}[^rank]")
    out.append("")
    out.append(asset_table(s["top"]) if s["top"] else "- 자료 없음")
    out.append("")
    out.append(f"## 콘텐츠별 하위 {TOP_N}")
    out.append("")
    out.append(asset_table(s["bottom"]) if s["bottom"] else "- 해당 없음(상위 표와 겹치지 않는, 조회가 수집된 콘텐츠가 없음)")
    out.append("")

    out.append("## 형식별 비교[^kind]")
    out.append("")
    krow = []
    for r in s["kinds"]:
        no_views = r["kind"] == "blog"
        krow.append([KIND_LABEL[r["kind"]], f_int(r["count"]), "—(원천 없음)" if no_views else f_int(r["views"]),
                     "—" if no_views else f_int(r["views_per_content"]), _miss(r["clicks"], fm), _miss(r["ctr"], fm, f_pct),
                     _miss(r["installs"], fm), _miss(r["paid"], fm), _miss(r["revenue_krw"], fm), _miss(r["revenue_per_1k_views"], fm)])
    out.append(_md_table(["형식", "콘텐츠 수", "조회", "콘텐츠당 조회", "클릭", "CTR", "설치", "유료 전환", "매출(원)", "1천 조회당 매출(원)"], krow))
    out.append("")
    out.append("- 이번 주에 활동(조회·클릭·설치·결제)이 있는 콘텐츠 기준이며, 지난 주차 콘텐츠도 포함합니다.")
    out.append("")

    if s["traffic"]:
        out.append("## 유튜브 트래픽 소스")
        out.append("")
        scopes = {"channel": "채널 전체", "short": "쇼츠", "longform": "롱폼"}
        trow = []
        for scope in ("channel", "short", "longform"):
            items = [t for t in s["traffic"] if t.get("scope") == scope]
            tot = sum(t["views"] or 0 for t in items)
            for t in items:
                trow.append([scopes[scope], t["source"], f_int(t["views"]), f_pct(ratio(t["views"], tot))])
        out.append(_md_table(["범위", "소스", "조회", "비율"], trow, left=2))
        out.append("")

    if s["notes"]:
        out.append("## 참고")
        out.append("")
        out += [f"- {n}" for n in s["notes"]]
        out.append("")

    out.append("## 계산식")
    out.append("")
    out += [
        ("[^views]: 조회 = 유튜브는 YouTube Analytics 기간 합계(views), 인스타·페북·틱톡은 누적 조회의 증분"
         "(이번 수집 누적 − 지난주 수집 누적; 지난주 값이 없으면 이번 주 게시물은 누적 전체). 티스토리는 수집 원천 없음. "
         "queue.json 에 없는 영상의 조회는 플랫폼 합계에만 들어갑니다."),
        "[^clicks]: 클릭 = 기간 안 link_clicks 행 수(추적 링크 /go 를 거친 클릭).",
        "[^ctr]: CTR = 클릭 ÷ 조회 × 100. 합계·플랫폼 CTR 의 클릭에는 조회 수집 원천이 없는 블로그·프로필 링크 클릭도 들어갑니다.",
        f"[^installs]: 설치 = 기간 안 app_installs 행 수(사용자당 1건). 귀속은 같은 원천·같은 콘텐츠 ID 의 클릭이 설치 전 {s['window_days']}일 안에 있을 때만.",
        "[^cvr]: 설치 전환율 = 설치 ÷ 클릭 × 100.",
        "[^trials]: 체험 시작 = INITIAL_PURCHASE 중 period_type=TRIAL.",
        "[^paid]: 유료 전환 = 첫 유료 결제(INITIAL_PURCHASE, 체험·프로모션 제외) + 체험→유료 전환(RENEWAL, is_trial_conversion) + 단건 구매(NON_RENEWING_PURCHASE).",
        (f"[^rev]: 매출(원) = (Σ 결제 금액 − Σ 환불 금액)[USD, price_in_usd] × {s['usd_krw']:,.0f}원. 결제 = 첫 유료 결제·갱신·단건, "
         "환불 = REFUND 또는 환불성 CANCELLATION(음수 금액·CUSTOMER_SUPPORT). 스토어 수수료·세금 차감 전 금액이며 SANDBOX·TEST 는 제외."),
        "[^rpm]: 1천 조회당 매출 = 매출(원) ÷ 조회 × 1,000.",
        (f"[^attr]: 미귀속 = utm 없음(iOS 등)·스토어 자연 유입·귀속 창({s['window_days']}일) 밖·원천 불명 설치. "
         "결제는 app_user_id 로 설치 귀속을 따르며, 설치 기록이 없는 사용자의 결제도 미귀속입니다."),
        "[^kind]: 형식별 CTR·1천 조회당 매출은 조회가 수집된 콘텐츠의 클릭·매출만으로 계산(조회 없는 지난 콘텐츠의 갱신 매출 제외).",
        "[^rank]: 순위 = 매출 → 유료 전환 → 설치 → 클릭 → 조회 순으로 큰 것부터. 하위 표는 조회가 수집된 콘텐츠 중 상위 표에 없는 것을 작은 것부터.",
        "[^wow]: 증감 = (이번 주 − 지난주) ÷ 지난주 × 100. 비율(CTR·전환율)은 차이를 %p 로. 지난주가 0 이면 '신규', 자료가 없으면 '—'.",
    ]
    out.append("")
    return "\n".join(out)


def _rpm(r: dict[str, Any]) -> float | None:
    v = ratio(r.get("revenue_krw"), r.get("views"))
    return v * 1000 if v is not None else None


def write_csv(s: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:   # 엑셀에서 한글이 깨지지 않게 BOM
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for c in s["content"]:
            vals = {**c, "week": s["week"], "ctr_pct": _pct_num(c["ctr"]), "cvr_install_pct": _pct_num(c["cvr_install"]),
                    "revenue_per_1k_views_krw": None if c["revenue_per_1k_views"] is None else round(c["revenue_per_1k_views"]),
                    "revenue_usd": None if c.get("revenue_usd") is None else round(c["revenue_usd"], 2)}
            w.writerow(["" if vals.get(col) is None else vals.get(col) for col in CSV_COLUMNS])
    tmp.replace(path)
    return path


def _pct_num(r: float | None) -> float | None:
    return None if r is None else round(r * 100, 2)


def write_report(week: str, cfg: Config | None = None) -> tuple[Path, Path, dict[str, Any]]:
    s = build(week, cfg)
    d = paths.reports_dir(week)
    md = d / "channel-revenue.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    tmp = md.with_suffix(".md.tmp")
    tmp.write_text(render_markdown(s), encoding="utf-8")
    tmp.replace(md)
    return md, write_csv(s, d / "channel-revenue.csv"), s
