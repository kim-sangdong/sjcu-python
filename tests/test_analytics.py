"""분석 모듈 테스트 — 귀속(손계산 대조), 보고서 수치·결손 경고, 광고 판단 규칙, --fake 전체 흐름.

STUDIO_ROOT 를 tmp_path 로 돌리고 네트워크를 쓰지 않는다.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from studio import cli as studio_cli
from studio import cliutil, config, paths
from studio.analytics import addecision, report
from studio.analytics.attribution import attribute
from studio.analytics.collect import load_metrics, metrics_path
from studio.analytics.common import Period, norm_source, settings, week_period
from studio.config import Config
from studio.models import write_json

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W30"            # 2026-07-20(월) ~ 07-27(월) 00:00 KST
PREV = "2026-W29"
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=paths.KST)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"),
                      ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    for k in ("STUDIO_DRY_RUN", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
              "YOUTUBE_REFRESH_TOKEN", "INSTAGRAM_TOKEN", "FACEBOOK_PAGE_TOKEN", "TIKTOK_REFRESH_TOKEN", "STUDIO_TOKEN_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


def main(argv: list[str]) -> int:
    """`python -m studio ...` 와 같은 진입점. cliutil 의 묶음 캐시(id 기준)가 이전 파서를 다시 잡지 않게 비우고 부른다."""
    cliutil._GROUPS.clear()
    return studio_cli.main(argv)


def _cfg(**extra) -> Config:
    data = {"app": {"name": "앱", "link_base": "https://ref.supabase.co/functions/v1/go"},
            "ads": {"weekly_budget_krw": 0, "cpm_krw": 4000, "cpi_krw": 2500}, **extra}
    return Config.model_validate(data)


def _period() -> Period:
    return week_period(WEEK, NOW)


def _funnel() -> dict:
    data = json.loads((FIX / "analytics_funnel_small.json").read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


# ---------------------------------------------------------------- 귀속 (손계산)
#
# 기간 안 클릭: S01·유튜브 4(5번은 기간 전), L1·유튜브 6, 인스타 프로필 2 → 12
# 설치(기간 안 6): u1→S01/yt, u2→L1/yt, u5→인스타(플랫폼), u3→창 밖(클릭이 10일 전), u4→utm 없음, u6→자연 유입. u7 은 기간 전 설치.
# 결제: e1 S01 첫 결제 $10, e2 L1 체험, e3 L1 체험→유료 $10, e4 S01 환불 −$10, e5 u4(미귀속) $5, e6 u7(W22-L1) 갱신 $10,
#       e7 SANDBOX 제외, e8 설치 없는 단건 7000원=$5, e9 기간 밖, e10 인스타 $20, e11 중복, e12 일반 해지, e13 TEST, e14 u3(창 밖) $10
# 합계: 결제 $70 − 환불 $10 = $60 → 84,000원. 유튜브 $20, 인스타 $20, 미귀속 $20.

def test_attribution_hand_calculated():
    d = _funnel()
    att = attribute(d["link_clicks"], d["app_installs"], d["purchases"], _period(), window_days=7, usd_krw=1400)
    t = att.total
    assert (t.clicks, t.installs, t.trials, t.paid, t.renewals, t.refunds, t.cancellations) == (12, 6, 1, 6, 1, 1, 1)
    assert t.gross_usd == pytest.approx(70)
    assert t.refund_usd == pytest.approx(10)
    assert t.to_dict(1400)["revenue_krw"] == 84000

    s01 = att.by_content[("2026W30-S01", "youtube")]
    assert (s01.clicks, s01.installs, s01.paid, s01.refunds, s01.cancellations) == (4, 1, 1, 1, 1)
    assert s01.revenue_usd == pytest.approx(0)          # 환불 차감

    l1 = att.by_content[("2026W30-L1", "youtube")]
    assert (l1.clicks, l1.installs, l1.trials, l1.paid) == (6, 1, 1, 1)
    assert l1.to_dict(1400)["revenue_krw"] == 14000

    old = att.by_content[("2026W22-L1", "youtube")]    # 기간 전 설치의 갱신 결제는 원래 콘텐츠로
    assert (old.installs, old.renewals, old.paid) == (0, 1, 0)
    assert old.to_dict(1400)["revenue_krw"] == 14000

    yt, ig = att.by_platform["youtube"], att.by_platform["instagram"]
    assert (yt.clicks, yt.installs, yt.paid, yt.renewals) == (10, 2, 2, 1)
    assert yt.to_dict(1400)["revenue_krw"] == 28000
    assert (ig.clicks, ig.installs, ig.paid) == (2, 1, 1)
    assert ig.to_dict(1400)["revenue_krw"] == 28000
    assert ("profile", "instagram") not in {(a, p) for a, p in att.by_content}   # 프로필 링크는 콘텐츠로 귀속하지 않음

    u = att.unattributed
    assert (u.installs, u.paid) == (3, 3)
    assert u.to_dict(1400)["revenue_krw"] == 28000      # $5 + 7000원($5) + $10
    assert dict(att.unattributed_installs) == {"window_expired": 1, "no_utm": 1, "organic": 1}
    assert dict(att.unattributed_events) == {"no_utm": 1, "no_install": 1, "window_expired": 1}
    assert dict(att.excluded) == {"sandbox": 1, "out_of_period": 1, "duplicate_event": 1, "test": 1, "duplicate_install": 1}
    # 플랫폼 합 + 미귀속 = 전체
    assert sum(f.revenue_usd for f in att.by_platform.values()) + u.revenue_usd == pytest.approx(t.revenue_usd)
    assert sum(f.installs for f in att.by_platform.values()) + u.installs == t.installs


def test_attribution_window_boundaries():
    d = _funnel()
    p = _period()
    wide = attribute(d["link_clicks"], d["app_installs"], d["purchases"], p, window_days=14)
    assert ("2026W29-S05", "youtube") in wide.by_content        # 10일 전 클릭 → 14일 창이면 귀속
    assert wide.by_content[("2026W29-S05", "youtube")].paid == 1
    assert "window_expired" not in wide.unattributed_installs

    loose = attribute(d["link_clicks"], d["app_installs"], d["purchases"], p, window_days=7, require_click=False)
    assert ("2026W29-S05", "youtube") in loose.by_content

    # 클릭이 정확히 7일 전이면 창 안, 7일 + 1분 전이면 창 밖
    clicks = [{"id": 1, "created_at": "2026-07-14T12:00:00+09:00", "source": "youtube", "content": "2026W30-S02"}]
    inside = [{"created_at": "2026-07-21T12:00:00+09:00", "app_user_id": "a", "utm_source": "youtube", "utm_content": "2026W30-S02"}]
    outside = [{"created_at": "2026-07-21T12:01:00+09:00", "app_user_id": "b", "utm_source": "youtube", "utm_content": "2026W30-S02"}]
    assert attribute(clicks, inside, [], p).by_content[("2026W30-S02", "youtube")].installs == 1
    out = attribute(clicks, outside, [], p)
    assert out.unattributed_installs["window_expired"] == 1 and not out.by_content
    # 다른 플랫폼의 같은 콘텐츠 클릭은 인정하지 않음(원천까지 맞아야 함)
    other = [{"id": 2, "created_at": "2026-07-21T11:00:00+09:00", "source": "instagram", "content": "2026W30-S02"}]
    assert attribute(other, inside, [], p).unattributed_installs["window_expired"] == 1


def test_refund_reversed_and_unknown_currency():
    p = _period()
    installs = [{"created_at": "2026-07-21T08:00:00+09:00", "app_user_id": "a", "utm_source": "yt_shorts", "utm_content": "2026W30-S03"}]
    clicks = [{"id": 1, "created_at": "2026-07-21T07:59:00+09:00", "source": "youtube", "content": "2026W30-S03"}]
    ev = [
        {"event_id": "1", "event_type": "INITIAL_PURCHASE", "app_user_id": "a", "period_type": "NORMAL", "price_in_usd": 10, "event_timestamp": "2026-07-21T09:00:00+09:00"},
        {"event_id": "2", "event_type": "REFUND", "app_user_id": "a", "price_in_usd": 10, "event_timestamp": "2026-07-22T09:00:00+09:00"},
        {"event_id": "3", "event_type": "REFUND_REVERSED", "app_user_id": "a", "price_in_usd": 10, "event_timestamp": "2026-07-23T09:00:00+09:00"},
        {"event_id": "4", "event_type": "INITIAL_PURCHASE", "app_user_id": "a", "period_type": "NORMAL", "price": 3000, "currency": "JPY", "event_timestamp": "2026-07-23T10:00:00+09:00"},
        {"event_id": "5", "event_type": "INITIAL_PURCHASE", "app_user_id": "a", "period_type": "PROMOTIONAL", "price_in_usd": 0, "event_timestamp": "2026-07-23T11:00:00+09:00"},
    ]
    att = attribute(clicks, installs, ev, p)
    f = att.by_content[("2026W30-S03", "youtube")]   # yt_shorts 도 유튜브로 본다
    assert f.revenue_usd == pytest.approx(10)          # +10 −10 +10, JPY 는 금액 모름
    assert f.paid == 2 and f.refunds == 1               # 프로모션은 유료 전환 아님
    assert att.excluded["unknown_price"] == 1
    assert any("금액을 알 수 없는" in w for w in att.warnings)


def test_norm_source_aliases():
    assert [norm_source(x) for x in ("youtube", "yt_long", "IG_REELS", "fb_reels", "tiktok", "tistory", "", "naver")] == \
        ["youtube", "youtube", "instagram", "facebook", "tiktok", "tistory", "", "other"]


# ---------------------------------------------------------------- 보고서 (손계산)

def _row(platform, remote_id, asset_id, kind, *, lifetime, views, published_at="2026-07-21T12:00:00+09:00"):
    return {"platform": platform, "remote_id": remote_id, "asset_id": asset_id, "kind": kind, "published_at": published_at,
            "url": None, "lifetime": lifetime, "views": views, "reach": None, "likes": None, "comments": None, "shares": None,
            "saves": None, "watch_minutes": None, "avg_view_seconds": None, "avg_view_pct": None, "subs_gained": None, "subs_lost": None}


def _src(rows=None, **kw):
    return {"ok": True, "fake": False, "dry_run": False, "error": None, "warnings": [], "rows": rows or [],
            "collected_at": "2026-08-01T12:00:00+09:00", "period_end": "2026-07-27T00:00:00+09:00", **kw}


def _metrics(week=WEEK, *, sources) -> dict:
    p = week_period(week, NOW)
    return {"schema": 1, "week": week, "period": p.to_json(), "collected_at": "2026-08-01T12:00:00+09:00", "fake": False,
            "settings": {"usd_krw": 1400, "click_window_days": 7}, "mapping": {"targets": 0, "warnings": []}, "sources": sources}


def _current_metrics() -> dict:
    return _metrics(sources={
        "youtube": _src([_row("youtube", "vidS01", "2026W30-S01", "short", lifetime=False, views=1000),
                         _row("youtube", "vidL1", "2026W30-L1", "longform", lifetime=False, views=2000),
                         _row("youtube", "vidX", None, None, lifetime=False, views=500)]),
        "instagram": _src([_row("instagram", "ig1", "2026W30-S01", "short", lifetime=True, views=300),
                           _row("instagram", "ig2", "2026W30-C01", "cardnews", lifetime=True, views=800,
                                published_at="2026-07-10T20:00:00+09:00")]),
        "facebook": {"ok": False, "error": "페이스북 테스트 실패 메시지", "warnings": [], "fake": False, "dry_run": False,
                     "collected_at": "2026-08-01T12:00:00+09:00"},
        # tiktok: 수집하지 않음(missing)
        "funnel": _src(data=_funnel()),
    })


def test_report_numbers_match_hand_calculation(root):
    write_json(metrics_path(WEEK), _current_metrics())
    s = report.build(WEEK, _cfg())
    k = s["kpi"]
    assert k["views"] == 4600                       # 유튜브 1000+2000+500(미매핑) + 인스타 300+800
    assert (k["clicks"], k["installs"], k["trials"], k["paid"], k["renewals"]) == (12, 6, 1, 6, 1)
    assert k["revenue_krw"] == 84000 and k["refund_krw"] == 14000
    assert k["unattributed_installs"] == 3
    assert k["ctr"] == pytest.approx(12 / 4600)
    assert s["view_missing"] == ["facebook", "tiktok"]

    plat = {r["platform"]: r for r in s["platforms"]}
    assert plat["youtube"]["views"] == 3500 and plat["youtube"]["clicks"] == 10
    assert plat["youtube"]["ctr"] == pytest.approx(10 / 3500)
    assert plat["youtube"]["cvr_install"] == pytest.approx(2 / 10)
    assert plat["youtube"]["revenue_per_1k_views"] == pytest.approx(28000 / 3500 * 1000)   # 8,000원
    assert plat["instagram"]["revenue_per_1k_views"] == pytest.approx(28000 / 1100 * 1000)
    assert plat["facebook"]["views"] is None and plat["facebook"]["view_state"] == "failed"
    assert plat["tistory"]["views"] is None and plat["tistory"]["view_state"] == "none"

    assets = {a["asset_id"]: a for a in s["assets"]}
    assert assets["2026W30-S01"]["views"] == 1300 and assets["2026W30-S01"]["revenue_krw"] == 0
    assert assets["2026W30-L1"]["revenue_per_1k_views"] == pytest.approx(7000)
    assert assets["2026W22-L1"]["views"] is None and assets["2026W22-L1"]["revenue_krw"] == 14000
    assert [a["asset_id"] for a in s["top"]] == ["2026W30-L1", "2026W22-L1", "2026W30-S01", "2026W30-C01"]

    kinds = {r["kind"]: r for r in s["kinds"]}
    assert kinds["longform"]["count"] == 2 and kinds["longform"]["revenue_krw"] == 28000
    assert kinds["longform"]["revenue_per_1k_views"] == pytest.approx(7000)    # 조회 없는 W22-L1 매출 제외
    assert kinds["short"]["views"] == 1300 and kinds["cardnews"]["views"] == 800
    assert kinds["blog"]["count"] == 0

    basis = {(r["platform"], r["remote_id"]): r["basis"] for r in report.period_rows(_current_metrics(), None)}
    assert basis[("instagram", "ig1")] == "new" and basis[("instagram", "ig2")] == "baseline_missing"

    md = report.render_markdown(s)
    assert "84,000" in md and "8,000" in md
    assert "페이스북: 수집 실패 — 페이스북 테스트 실패 메시지" in md
    assert "틱톡: 이번 수집에 없습니다" in md
    assert "지난주 수집값이 없는 이전 게시물 1개" in md
    assert "일부 결손: 페이스북, 틱톡" in md
    assert "[^rev]:" in md and "× 1,400원" in md and "[^ctr]: CTR = 클릭 ÷ 조회 × 100." in md


def test_report_week_over_week_and_lifetime_delta(root):
    write_json(metrics_path(WEEK), _current_metrics())
    write_json(metrics_path(PREV), _metrics(PREV, sources={
        "youtube": _src([_row("youtube", "vidOld", "2026W29-S01", "short", lifetime=False, views=2000)]),
        "instagram": _src([_row("instagram", "ig2", "2026W30-C01", "cardnews", lifetime=True, views=500,
                                published_at="2026-07-10T20:00:00+09:00")]),
        "facebook": _src(), "tiktok": _src(), "funnel": _src(data={"link_clicks": [], "app_installs": [], "purchases": []}),
    }))
    s = report.build(WEEK, _cfg())
    assert s["kpi"]["views"] == 4100                # 인스타 C01 은 800 − 500 = 300 (증분)
    assert s["previous"]["kpi"]["views"] == 2500    # 지난주: 유튜브 2000 + 인스타 500(기준 없음 → 누적)
    assert report.change(s["kpi"]["views"], s["previous"]["kpi"]["views"]) == "+64.0%"
    assert report.change(12, 0) == "신규" and report.change(None, 3) == "—"
    md = report.render_markdown(s)
    assert "| 조회[^views] | 4,100 (일부 결손: 페이스북, 틱톡) | 2,500 | +64.0% |" in md
    assert "| 클릭[^clicks] | 12 | 0 | 신규 |" in md


def test_report_marks_missing_funnel_as_gap_not_zero(root):
    m = _current_metrics()
    m["sources"]["funnel"] = {"ok": False, "error": "[설정 필요] 환경변수 SUPABASE_URL 가 없습니다.", "warnings": [],
                              "fake": False, "dry_run": False}
    write_json(metrics_path(WEEK), m)
    s = report.build(WEEK, _cfg())
    assert s["kpi"]["clicks"] is None and s["kpi"]["revenue_krw"] is None
    md = report.render_markdown(s)
    assert "| 클릭[^clicks] | 결손 |" in md and "| 매출(원)[^rev] | 결손 |" in md
    assert "클릭·설치·결제(Supabase): 수집 실패" in md
    data = addecision.build_ad_data(WEEK, _cfg())
    assert {it["rule_recommendation"] for it in data["items"]} == {"insufficient_data"}
    assert any("결손" in it["reason"] for it in data["items"])


def test_report_cli_requires_metrics(root, capsys):
    assert main(["analytics", "report", "--week", WEEK]) == 1
    assert "collect" in capsys.readouterr().err


# ---------------------------------------------------------------- 광고 판단 규칙

RULES = settings(_cfg()).rules


@pytest.mark.parametrize("row, want", [
    ({"views": 20000, "clicks": 300, "installs": 40, "paid": 8, "revenue_krw": 400000}, "boost"),       # ROAS min(5.0, 4.0)=4.0
    ({"views": 100000, "clicks": 500, "installs": 50, "paid": 10, "revenue_krw": 300000}, "hold"),      # min(0.75, 2.4)=0.75
    ({"views": 100000, "clicks": 500, "installs": 50, "paid": 1, "revenue_krw": 30000}, "stop"),        # min(0.075, 0.24)
    ({"views": 5000, "clicks": 200, "installs": 30, "paid": 0, "revenue_krw": 0}, "stop"),              # 유료 전환 0
    ({"views": 1000, "clicks": 300, "installs": 30, "paid": 2, "revenue_krw": 300000}, "hold"),         # ROAS 높지만 유료 2 < 3
    ({"views": 9000, "clicks": 99, "installs": 50, "paid": 9, "revenue_krw": 900000}, "insufficient_data"),
    ({"views": 9000, "clicks": 150, "installs": 19, "paid": 9, "revenue_krw": 900000}, "insufficient_data"),
])
def test_rule_recommendation(row, want):
    est = addecision.estimates(row["views"], row["installs"], row["paid"], row["revenue_krw"], 4000, 2500)
    rec, reason, sample_ok = addecision.recommend(row, RULES, est)
    assert rec == want, reason
    assert sample_ok == (want != "insufficient_data")


def test_estimates_hand_calculated():
    est = addecision.estimates(100000, 50, 10, 300000, 4000, 2500)
    assert est["cpm_basis"] == {"cost_krw": 400000, "cac_krw": 40000, "roas": 0.75}
    assert est["cpi_basis"] == {"cost_krw": 125000, "cac_krw": 12500, "roas": 2.4}
    assert est["est_cac_krw"] == 40000 and est["est_roas"] == 0.75
    none = addecision.estimates(None, 0, 0, 0, 4000, 2500)
    assert none["est_roas"] is None and none["est_cac_krw"] is None


def test_rules_overridable_from_config():
    st = settings(_cfg(ads={"rules": {"min_clicks": 10, "boost_roas": 3}, "cpm_krw": 1000}, analytics={"usd_krw": 1300}))
    assert st.rules["min_clicks"] == 10 and st.rules["boost_roas"] == 3 and st.rules["min_installs"] == 20
    assert st.cpm_krw == 1000 and st.cpi_krw == 2500 and st.usd_krw == 1300


def test_ad_data_items_hand_calculated(root):
    write_json(metrics_path(WEEK), _current_metrics())
    data = addecision.build_ad_data(WEEK, _cfg())
    item = next(i for i in data["items"] if i["asset_id"] == "2026W30-L1" and i["platform"] == "youtube")
    assert (item["views"], item["clicks"], item["installs"], item["paid"], item["revenue_krw"]) == (2000, 6, 1, 1, 14000)
    assert item["ctr"] == pytest.approx(0.003) and item["cvr_install"] == pytest.approx(1 / 6, abs=1e-6)
    assert item["revenue_per_1k_views"] == 7000
    assert item["est_cac_krw"] == 8000 and item["est_roas"] == 1.75      # CPM 기준 8,000원 / CPI 기준 2,500원
    assert item["sample_ok"] is False and item["rule_recommendation"] == "insufficient_data"
    assert item["organic_rank"] == 1
    assert data["assumptions"]["cpm_krw"] == 4000 and data["weekly_budget_krw"] == 0
    assert any("페이스북" in w for w in data["data_warnings"])


# ---------------------------------------------------------------- --fake 전체 흐름

def _write_channel(root: Path, link_base: str = "https://ref.supabase.co/functions/v1/go") -> None:
    (root / "channel.yaml").write_text(f"app:\n  link_base: \"{link_base}\"\nads:\n  cpm_krw: 4000\n  cpi_krw: 2500\n", encoding="utf-8")
    config.load_config.cache_clear()


def test_fake_flow_collect_report_ad_data(root, capsys):
    _write_channel(root)
    assert main(["analytics", "collect", "--week", WEEK, "--fake"]) == 0
    m = load_metrics(WEEK)
    assert m["fake"] is True
    assert set(m["sources"]) == {"youtube", "instagram", "facebook", "tiktok", "funnel"}
    assert all(s["ok"] and s["fake"] for s in m["sources"].values())
    yt = m["sources"]["youtube"]["rows"]
    assert any(r["asset_id"] == "2026W30-L1" for r in yt) and any(r["asset_id"] is None for r in yt)

    assert main(["analytics", "report", "--week", WEEK]) == 0
    md = (paths.reports_dir(WEEK) / "channel-revenue.md").read_text(encoding="utf-8")
    for heading in ("## 데이터 결손 경고", "## 주간 KPI", "## 플랫폼별 깔때기", "## 콘텐츠별 상위 10", "## 콘텐츠별 하위 10",
                    "## 형식별 비교", "## 유튜브 트래픽 소스", "## 계산식"):
        assert heading in md
    assert "합성(--fake) 데이터" in md
    with open(paths.reports_dir(WEEK) / "channel-revenue.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and set(report.CSV_COLUMNS) == set(rows[0])

    # 보고서 수치가 귀속 결과와 맞는지(내부 일관성)
    s = report.build(WEEK)
    att = s["attribution"]
    assert sum(v["installs"] for v in att["by_platform"].values()) + att["unattributed"]["installs"] == s["kpi"]["installs"]
    assert sum(v["revenue_krw"] for v in att["by_platform"].values()) + att["unattributed"]["revenue_krw"] == \
        pytest.approx(s["kpi"]["revenue_krw"], abs=3)
    assert sum(c["clicks"] for c in s["content"]) <= s["kpi"]["clicks"]
    assert {"window_expired", "no_utm", "organic"} <= set(att["unattributed_installs"])
    assert att["excluded"].get("sandbox") == 1 and s["kpi"]["refund_krw"] > 0 and s["kpi"]["renewals"] >= 3
    assert len(rows) == len(s["content"])

    assert main(["analytics", "ad-data", "--week", WEEK]) == 0
    data = json.loads((paths.reports_dir(WEEK) / "ad-decision-data.json").read_text(encoding="utf-8"))
    keys = {"asset_id", "platform", "views", "clicks", "ctr", "installs", "cvr_install", "paid", "revenue_krw",
            "revenue_per_1k_views", "sample_ok", "est_cac_krw", "est_roas", "rule_recommendation", "reason"}
    assert data["items"] and all(keys <= set(i) for i in data["items"])
    assert {i["rule_recommendation"] for i in data["items"]} <= set(addecision.RECOMMENDATIONS)
    assert data["fake"] is True and sorted(i["organic_rank"] for i in data["items"]) == list(range(1, len(data["items"]) + 1))

    # 같은 주차는 같은 합성 숫자(결정적)
    before = json.dumps(load_metrics(WEEK)["sources"]["funnel"]["data"], sort_keys=True)
    assert main(["analytics", "collect", "--week", WEEK, "--fake"]) == 0
    assert json.dumps(load_metrics(WEEK)["sources"]["funnel"]["data"], sort_keys=True) == before


def test_collect_partial_failure_is_recorded_and_others_continue(root, monkeypatch, capsys):
    _write_channel(root)
    from studio.analytics.collect import instagram as ig_mod

    def boom(ctx):
        raise RuntimeError("인스타 서버가 터졌습니다")

    monkeypatch.setattr(ig_mod, "collect", boom)
    assert main(["analytics", "collect", "--week", WEEK, "--fake"]) == 2
    m = load_metrics(WEEK)
    assert m["sources"]["instagram"]["ok"] is False and "터졌습니다" in m["sources"]["instagram"]["error"]
    assert m["sources"]["youtube"]["ok"] and m["sources"]["funnel"]["ok"]
    assert main(["analytics", "report", "--week", WEEK]) == 0
    md = (paths.reports_dir(WEEK) / "channel-revenue.md").read_text(encoding="utf-8")
    assert "인스타그램: 수집 실패 — 인스타 서버가 터졌습니다" in md
    assert "| 인스타그램 | 결손 |" in md


def test_collect_merges_sources_and_respects_max_age(root, capsys):
    _write_channel(root)
    assert main(["analytics", "collect", "--week", WEEK, "--fake", "--sources", "youtube"]) == 0
    assert main(["analytics", "collect", "--week", WEEK, "--fake", "--sources", "funnel"]) == 0
    m = load_metrics(WEEK)
    assert {"youtube", "funnel"} <= set(m["sources"])
    stamp = metrics_path(WEEK).stat().st_mtime_ns
    capsys.readouterr()
    assert main(["analytics", "collect", "--week", WEEK, "--fake", "--sources", "youtube,funnel", "--max-age-hours", "6"]) == 0
    assert "다시 수집하지 않습니다" in capsys.readouterr().out
    assert metrics_path(WEEK).stat().st_mtime_ns == stamp
    assert main(["analytics", "collect", "--week", WEEK, "--sources", "nope"]) == 1


def test_fake_refuses_to_overwrite_real_data(root, capsys):
    _write_channel(root)
    write_json(metrics_path(WEEK), _current_metrics())
    assert main(["analytics", "collect", "--week", WEEK, "--fake"]) == 1
    assert "덮어쓰지 않습니다" in capsys.readouterr().err
    assert load_metrics(WEEK)["fake"] is False
    assert main(["analytics", "collect", "--week", WEEK, "--fake", "--force"]) == 0
    m = load_metrics(WEEK)
    assert m["fake"] is True and all(s["fake"] for s in m["sources"].values())


def test_future_week_is_rejected(root, capsys):
    _write_channel(root)
    assert main(["analytics", "collect", "--week", "2099-W01", "--fake"]) == 1
    assert "아직 시작하지 않은" in capsys.readouterr().err


def test_failed_recollection_keeps_previous_good_data(root, monkeypatch, capsys):
    _write_channel(root)
    assert main(["analytics", "collect", "--week", WEEK, "--fake"]) == 0
    before = load_metrics(WEEK)["sources"]["youtube"]
    from studio.analytics.collect import youtube as yt_mod

    def boom(ctx):
        raise RuntimeError("유튜브 일시 오류")

    monkeypatch.setattr(yt_mod, "collect", boom)
    assert main(["analytics", "collect", "--week", WEEK, "--fake", "--sources", "youtube"]) == 1
    assert "이전 수집본" in capsys.readouterr().out
    after = load_metrics(WEEK)["sources"]["youtube"]
    assert after["ok"] and after["rows"] == before["rows"] and after["last_error"] == "유튜브 일시 오류"
    s = report.build(WEEK)
    assert any("최근 수집" in w and "유튜브 일시 오류" in w for w in s["warnings"])


def test_youtube_revenue_only_when_enabled(root):
    (root / "channel.yaml").write_text("app:\n  link_base: https://x/go\nanalytics:\n  youtube_revenue: true\n  usd_krw: 1300\n",
                                       encoding="utf-8")
    config.load_config.cache_clear()
    assert main(["analytics", "collect", "--week", WEEK, "--fake", "--sources", "youtube,funnel"]) == 0
    m = load_metrics(WEEK)
    assert m["settings"]["usd_krw"] == 1300
    assert any((r.get("extra") or {}).get("yt_revenue_usd") is not None for r in m["sources"]["youtube"]["rows"])
    s = report.build(WEEK)
    assert s["kpi"]["yt_revenue_usd"] is not None and s["usd_krw"] == 1300
    md = report.render_markdown(s)
    assert "유튜브 광고 수익(추정)" in md and "1 USD = 1,300원" in md

    (root / "channel.yaml").write_text("app:\n  link_base: https://x/go\n", encoding="utf-8")
    config.load_config.cache_clear()
    assert main(["analytics", "collect", "--week", WEEK, "--fake", "--sources", "youtube"]) == 0
    assert all("extra" not in r for r in load_metrics(WEEK)["sources"]["youtube"]["rows"])


def test_trial_alias_events_and_sandbox_opt_in():
    p = _period()
    installs = [{"created_at": "2026-07-21T08:00:00+09:00", "app_user_id": "a", "utm_source": "tistory", "utm_content": "2026W30-B01"}]
    clicks = [{"id": 1, "created_at": "2026-07-21T07:00:00+09:00", "source": "tistory", "content": "2026W30-B01"}]
    ev = [
        {"event_id": "1", "event_type": "TRIAL_STARTED", "app_user_id": "a", "event_timestamp": "2026-07-21T09:00:00+09:00"},
        {"event_id": "2", "event_type": "TRIAL_CONVERTED", "app_user_id": "a", "price_in_usd": 4, "event_timestamp": "2026-07-24T09:00:00+09:00"},
        {"event_id": "3", "event_type": "RENEWAL", "app_user_id": "a", "price_in_usd": 4, "environment": "SANDBOX",
         "event_timestamp": "2026-07-25T09:00:00+09:00"},
    ]
    f = attribute(clicks, installs, ev, p).by_content[("2026W30-B01", "tistory")]
    assert (f.trials, f.paid, f.renewals, f.revenue_usd) == (1, 1, 0, 4)
    f2 = attribute(clicks, installs, ev, p, include_sandbox=True).by_content[("2026W30-B01", "tistory")]
    assert (f2.renewals, f2.revenue_usd) == (1, 8)
