"""광고 판단 데이터 — reports/<week>/ad-decision-data.json (/ad-decision 이 해석한다).

콘텐츠×플랫폼마다 표본 충분 여부, 가정값(CPM·CPI)으로 추정한 CAC·ROAS, 규칙 권고(boost|hold|stop|insufficient_data)를 낸다.
규칙 기본값은 common.DEFAULT_RULES, 덮어쓰기는 channel.yaml ads.rules.

추정 방법(두 가지를 모두 내고, 판단에는 보수적인 값을 쓴다)
- CPM 기준: 같은 조회를 광고로 샀다면 든 비용 = 조회 ÷ 1000 × cpm_krw.  ROAS = 매출 ÷ 그 비용.
- CPI 기준: 같은 설치를 광고로 샀다면 든 비용 = 설치 × cpi_krw.       ROAS = 매출 ÷ 그 비용.
- CAC(유료 고객 1명 확보 비용) = 그 비용 ÷ 유료 전환. 대표값: est_cac_krw = 둘 중 큰 값, est_roas = 둘 중 작은 값.
- 가정: 광고로 산 조회·설치가 유기적 조회·설치와 같은 비율로 결제한다(실제로는 대개 더 낮다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from studio import paths
from studio.analytics.common import DEFAULT_RULES, iso_now, settings
from studio.analytics.report import build
from studio.config import Config, load_config
from studio.models import write_json

RECOMMENDATIONS = ("boost", "hold", "stop", "insufficient_data")


def estimates(views: float | None, installs: float | None, paid: float | None, revenue_krw: float | None,
              cpm_krw: float, cpi_krw: float) -> dict[str, Any]:
    cost_cpm = views / 1000 * cpm_krw if views and cpm_krw > 0 else None
    cost_cpi = installs * cpi_krw if installs and cpi_krw > 0 else None
    rev = revenue_krw or 0

    def per(cost: float | None, n: float | None) -> float | None:
        return round(cost / n) if cost is not None and n else None

    def roas(cost: float | None) -> float | None:
        return round(rev / cost, 4) if cost else None

    out = {
        "cpm_basis": {"cost_krw": None if cost_cpm is None else round(cost_cpm), "cac_krw": per(cost_cpm, paid), "roas": roas(cost_cpm)},
        "cpi_basis": {"cost_krw": None if cost_cpi is None else round(cost_cpi), "cac_krw": per(cost_cpi, paid), "roas": roas(cost_cpi)},
    }
    cacs = [b["cac_krw"] for b in out.values() if b["cac_krw"] is not None]
    roases = [b["roas"] for b in out.values() if b["roas"] is not None]
    out["est_cac_krw"] = max(cacs) if cacs else None
    out["est_roas"] = min(roases) if roases else None
    return out


def recommend(row: dict[str, Any], rules: dict[str, float], est: dict[str, Any]) -> tuple[str, str, bool]:
    """(권고, 이유, 표본 충분). row 에 clicks·installs·paid 가 있어야 한다."""
    clicks, installs, paid = row.get("clicks") or 0, row.get("installs") or 0, row.get("paid") or 0
    min_c, min_i = rules["min_clicks"], rules["min_installs"]
    sample_ok = clicks >= min_c and installs >= min_i
    if not sample_ok:
        return "insufficient_data", f"표본 부족: 클릭 {clicks:,.0f}/{min_c:,.0f}, 설치 {installs:,.0f}/{min_i:,.0f}.", False
    roas = est.get("est_roas")
    if paid <= 0:
        return "stop", f"설치 {installs:,.0f}건에서 유료 전환이 0건 — 광고비를 회수할 근거가 없습니다.", True
    if roas is None:
        return "hold", "추정 ROAS 를 계산할 수 없습니다(조회·설치 자료 부족).", True
    if roas < rules["stop_roas"]:
        return "stop", f"추정 ROAS {roas:.2f} < 중단 기준 {rules['stop_roas']:.2f}.", True
    if roas >= rules["boost_roas"] and paid >= rules["min_paid_for_boost"]:
        return "boost", f"추정 ROAS {roas:.2f} ≥ {rules['boost_roas']:.2f}, 유료 전환 {paid:,.0f}건 ≥ {rules['min_paid_for_boost']:,.0f}.", True
    if roas >= rules["boost_roas"]:
        return "hold", f"추정 ROAS {roas:.2f} 는 높지만 유료 전환 {paid:,.0f}건 < {rules['min_paid_for_boost']:,.0f} — 우연일 수 있어 지켜봅니다.", True
    return "hold", f"추정 ROAS {roas:.2f} 가 중단 기준({rules['stop_roas']:.2f})과 부스트 기준({rules['boost_roas']:.2f}) 사이.", True


def _r(v: float | None, digits: int = 4) -> float | None:
    return None if v is None else round(v, digits)


def build_ad_data(week: str, cfg: Config | None = None, *, budget_krw: float | None = None,
                  summary: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    st = settings(cfg)
    s = summary or build(week, cfg)
    items = []
    for c in s["content"]:
        est = estimates(c["views"], c["installs"], c["paid"], c["revenue_krw"], st.cpm_krw, st.cpi_krw)
        if s["funnel_ok"]:
            rec, reason, sample_ok = recommend(c, st.rules, est)
        else:
            rec, reason, sample_ok = "insufficient_data", "클릭·설치·결제(funnel) 수집 결손 — 판단할 수 없습니다.", False
        items.append({
            "asset_id": c["asset_id"],
            "kind": c["kind"],
            "content_week": c["content_week"],
            "platform": c["platform"],
            "views": c["views"],
            "views_basis": c.get("views_basis"),
            "clicks": c["clicks"],
            "ctr": _r(c["ctr"], 6),
            "installs": c["installs"],
            "cvr_install": _r(c["cvr_install"], 6),
            "trials": c["trials"],
            "paid": c["paid"],
            "revenue_krw": c["revenue_krw"],
            "revenue_per_1k_views": _r(c["revenue_per_1k_views"], 2),
            "sample_ok": sample_ok,
            "est_cac_krw": est["est_cac_krw"],
            "est_roas": est["est_roas"],
            "estimates": {"cpm_basis": est["cpm_basis"], "cpi_basis": est["cpi_basis"]},
            "rule_recommendation": rec,
            "reason": reason,
        })
    # 유기적으로 밀 순서(예산 0 일 때 /ad-decision 이 그대로 쓴다): 매출 → 유료 전환 → 설치 → 1천 조회당 매출 → 조회
    order = sorted(items, key=lambda x: (x["revenue_krw"] or 0, x["paid"] or 0, x["installs"] or 0,
                                         x["revenue_per_1k_views"] or 0, x["views"] or 0), reverse=True)
    for i, it in enumerate(order, 1):
        it["organic_rank"] = i
    counts = {r: sum(1 for it in items if it["rule_recommendation"] == r) for r in RECOMMENDATIONS}
    return {
        "week": week,
        "generated_at": iso_now(),
        "fake": s["fake"],
        "period": s["period"],
        "weekly_budget_krw": st.weekly_budget_krw if budget_krw is None else budget_krw,
        "assumptions": {
            "cpm_krw": st.cpm_krw,
            "cpi_krw": st.cpi_krw,
            "usd_krw": s["usd_krw"],
            "click_window_days": s["window_days"],
            "rules": st.rules,
            "rule_defaults": DEFAULT_RULES,
            "note": "CPM·CPI 는 channel.yaml ads.* 가정값(실제 집행 후 보정). 광고 유입이 유기적 유입과 같은 비율로 결제한다고 가정. "
                    "매출은 스토어 수수료·세금 차감 전.",
        },
        "definitions": {
            "ctr": "클릭 ÷ 조회 (0~1 비율)",
            "cvr_install": "설치 ÷ 클릭 (0~1 비율)",
            "revenue_per_1k_views": "매출(원) ÷ 조회 × 1000",
            "sample_ok": f"클릭 ≥ {st.rules['min_clicks']:,.0f} 그리고 설치 ≥ {st.rules['min_installs']:,.0f}",
            "est_cac_krw": "max(CPM 기준 CAC, CPI 기준 CAC). CPM 기준 = 조회÷1000×cpm_krw÷유료 전환, CPI 기준 = 설치×cpi_krw÷유료 전환",
            "est_roas": "min(CPM 기준 ROAS, CPI 기준 ROAS). CPM 기준 = 매출÷(조회÷1000×cpm_krw), CPI 기준 = 매출÷(설치×cpi_krw)",
            "rule_recommendation": "표본 부족→insufficient_data; 유료 전환 0 또는 ROAS<stop_roas→stop; "
                                   "ROAS≥boost_roas 이고 유료 전환≥min_paid_for_boost→boost; 그 밖→hold",
            "organic_rank": "매출→유료 전환→설치→1천 조회당 매출→조회 순 정렬 순위",
        },
        "data_warnings": s["warnings"],
        "summary": {"items": len(items), **counts, "kpi": s["kpi"]},
        "items": sorted(items, key=lambda x: x["organic_rank"]),
    }


def write_ad_data(week: str, cfg: Config | None = None, *, budget_krw: float | None = None) -> tuple[Path, dict[str, Any]]:
    data = build_ad_data(week, cfg, budget_krw=budget_krw)
    path = paths.reports_dir(week) / "ad-decision-data.json"
    write_json(path, data)
    return path, data
