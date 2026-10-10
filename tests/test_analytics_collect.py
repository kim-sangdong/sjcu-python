"""분석 수집기 HTTP 테스트 — 실제 API 대신 responses 로 응답을 흉내 낸다.

유튜브(쪽 나눔·지표 거절 시 기본 지표로 재시도·권한 오류), 인스타(지표 하나 거절 시 하나씩 재시도·토큰 오류),
페이스북(소셜 지표 해석), 틱톡(20개 묶음·게시물 ID 아닌 값), Supabase(Range 쪽 나눔·in 따옴표·해시), dry-run(네트워크 없음).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from studio import config, paths
from studio.analytics import common
from studio.analytics.collect import funnel as funnel_mod
from studio.analytics.collect import instagram as ig_mod
from studio.analytics.collect import load_metrics, run
from studio.analytics.collect import youtube as yt_mod
from studio.analytics.collect.funnel import hash_uid, pg_in
from studio.config import Config
from studio.models import PublishQueue, PublishTarget, write_model

FIX = Path(__file__).parent / "fixtures"
WEEK = "2026-W30"
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=paths.KST)
YT = yt_mod.REPORTS_URL
IG = "https://graph.instagram.com/v26.0"
FB = "https://graph.facebook.com/v26.0"
TT = "https://open.tiktokapis.com/v2/video/query/"
SB = "https://ref.supabase.co"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("ARCHIVE", "archive"), ("REPORTS", "reports"),
                      ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    for k in ("STUDIO_DRY_RUN", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "INSTAGRAM_TOKEN", "FACEBOOK_PAGE_TOKEN",
              "STUDIO_TOKEN_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(common, "_sleep", lambda s: None)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


def _cfg() -> Config:
    return Config.model_validate({"app": {"link_base": "https://ref.supabase.co/functions/v1/go"}})


def _queue(targets: list[tuple[str, str, str, str]], week: str = WEEK) -> None:
    """(asset_id, kind, platform, remote_id) 목록 → content/weeks/<week>/queue.json (모두 공개됨)."""
    at = datetime(2026, 7, 21, 12, 0, tzinfo=paths.KST)
    q = PublishQueue(week=week, targets=[
        PublishTarget(asset_id=a, asset_kind=k, platform=p, publish_at=at, status="published", remote_id=r)
        for a, k, p, r in targets])
    write_model(paths.content_dir(week) / "queue.json", q)


def _qs(request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(request.url).query).items()}


def _table(heads: list[str], rows: list[list]) -> dict:
    return {"kind": "youtubeAnalytics#resultTable", "columnHeaders": [{"name": h} for h in heads], "rows": rows}


# ---------------------------------------------------------------- 유튜브

@pytest.fixture
def yt_creds(monkeypatch):
    from studio.publish import google_auth

    seen = {}

    def creds(scopes=None):
        seen["scopes"] = scopes
        return SimpleNamespace(token="ya29.test-access-token")

    monkeypatch.setattr(google_auth, "credentials", creds)
    return seen


def test_youtube_paginates_maps_queue_and_traffic(root, rsps, yt_creds, monkeypatch):
    monkeypatch.setattr(yt_mod, "MAX_RESULTS", 2)
    _queue([("2026W30-S01", "short", "youtube", "vidS01"), ("2026W30-L1", "longform", "youtube", "vidL1")])
    calls = []

    def cb(request):
        q = _qs(request)
        calls.append(q)
        assert request.headers["Authorization"] == "Bearer ya29.test-access-token"
        assert q["ids"] == "channel==MINE" and q["startDate"] == "2026-07-20" and q["endDate"] == "2026-07-26"
        metrics = q["metrics"].split(",")
        if q["dimensions"] == "video":
            pages = {"1": [["vidL1", 2000], ["vidS01", 1000]], "3": [["vidX", 500]]}
            rows = [[v, views, *[7 for _ in metrics[1:]]] for v, views in pages[q["startIndex"]]]
            return 200, {}, json.dumps(_table(["video", *metrics], rows))
        if q.get("filters") == "video==vidS01":
            return 200, {}, json.dumps(_table([q["dimensions"], *metrics], [["SHORTS", 900, 10], ["YT_SEARCH", 100, 5]]))
        if q.get("filters") == "video==vidL1":
            return 200, {}, json.dumps(_table([q["dimensions"], *metrics], [["SUGGESTED", 1500, 300], ["YT_SEARCH", 500, 90]]))
        return 200, {}, json.dumps(_table([q["dimensions"], *metrics], [["SHORTS", 1400, 20], ["SUGGESTED", 1600, 300]]))

    rsps.add_callback(responses.GET, YT, callback=cb)
    m, _ = run(WEEK, sources=["youtube"], cfg=_cfg(), now=NOW)
    res = m["sources"]["youtube"]
    assert res["ok"], res
    assert [c.get("startIndex") for c in calls if c["dimensions"] == "video"] == ["1", "3"]
    rows = {r["remote_id"]: r for r in res["rows"]}
    assert rows["vidS01"]["asset_id"] == "2026W30-S01" and rows["vidS01"]["kind"] == "short"
    assert rows["vidL1"]["views"] == 2000 and rows["vidL1"]["lifetime"] is False and rows["vidL1"]["avg_view_pct"] == 7
    assert rows["vidX"]["asset_id"] is None
    assert any("queue.json 에 없는 영상 1개" in w for w in res["warnings"])
    scopes = {(t["scope"], t["source"]): t["views"] for t in res["traffic_sources"]}
    assert scopes[("channel", "SUGGESTED")] == 1600 and scopes[("short", "SHORTS")] == 900 and scopes[("longform", "SUGGESTED")] == 1500
    assert yt_creds["scopes"] is None          # 수익 지표를 끄면 기본 범위


def test_youtube_falls_back_to_core_metrics_on_400(root, rsps, yt_creds):
    _queue([("2026W30-S01", "short", "youtube", "vidS01")])

    def cb(request):
        q = _qs(request)
        metrics = q["metrics"].split(",")
        if q["dimensions"] == "video" and "averageViewPercentage" in metrics:
            return 400, {}, json.dumps({"error": {"code": 400, "message": "Unknown identifier (averageViewPercentage)"}})
        if q["dimensions"] == "video":
            return 200, {}, json.dumps(_table(["video", *metrics], [["vidS01", 1234, 50.5, 3]]))
        return 200, {}, json.dumps(_table([q["dimensions"], *metrics], []))

    rsps.add_callback(responses.GET, YT, callback=cb)
    m, _ = run(WEEK, sources=["youtube"], cfg=_cfg(), now=NOW)
    res = m["sources"]["youtube"]
    assert res["ok"]
    row = res["rows"][0]
    assert row["views"] == 1234 and row["watch_minutes"] == 50.5 and row["subs_gained"] == 3 and row["avg_view_pct"] is None
    assert any("기본 지표" in w and "Unknown identifier" in w for w in res["warnings"])


def test_youtube_permission_error_fails_source_with_hint(root, rsps, yt_creds):
    rsps.add(responses.GET, YT, status=403, json={"error": {"code": 403, "message": "Insufficient Permission"}})
    m, _ = run(WEEK, sources=["youtube"], cfg=_cfg(), now=NOW)
    res = m["sources"]["youtube"]
    assert res["ok"] is False
    assert "auth youtube" in res["error"] and "ya29" not in res["error"]


# ---------------------------------------------------------------- 인스타·페북

def test_instagram_retries_metrics_one_by_one_and_remembers_rejected(root, rsps, monkeypatch):
    monkeypatch.setattr(ig_mod, "_token", lambda ctx: "IGAA-secret-token-123")
    _queue([("2026W30-S01", "short", "instagram", "1801"), ("2026W30-S02", "short", "instagram", "1802"),
            ("2026W30-C01", "cardnews", "instagram", "1803")])
    calls = []
    values = {"views": 900, "reach": 700, "likes": 40, "comments": 3, "shares": 5, "saved": 9, "total_interactions": 57}

    def cb(request):
        q = _qs(request)
        assert q["access_token"] == "IGAA-secret-token-123"
        metrics = q["metric"].split(",")
        calls.append((urlparse(request.url).path, metrics))
        if "ig_reels_avg_watch_time" in metrics:
            return 400, {}, json.dumps({"error": {"code": 100, "message": "(#100) metric ig_reels_avg_watch_time is not supported"}})
        return 200, {}, json.dumps({"data": [{"name": n, "period": "lifetime", "values": [{"value": values[n]}]} for n in metrics]})

    for mid in ("1801", "1802", "1803"):
        rsps.add_callback(responses.GET, f"{IG}/{mid}/insights", callback=cb)
    m, _ = run(WEEK, sources=["instagram"], cfg=_cfg(), now=NOW)
    res = m["sources"]["instagram"]
    assert res["ok"], res
    rows = {r["asset_id"]: r for r in res["rows"]}
    assert rows["2026W30-S01"]["views"] == 900 and rows["2026W30-S01"]["saves"] == 9 and rows["2026W30-S01"]["lifetime"] is True
    assert rows["2026W30-S01"]["avg_view_seconds"] is None
    assert rows["2026W30-C01"]["reach"] == 700
    assert any("ig_reels_avg_watch_time" in w for w in res["warnings"])
    # 두 번째 릴스부터는 거절된 지표를 빼고 한 번에 요청
    s02 = [mets for path, mets in calls if path.endswith("/1802/insights")]
    assert s02 == [["views", "reach", "likes", "comments", "shares", "saved", "total_interactions"]]


def test_instagram_token_error_fails_source(root, rsps, monkeypatch):
    monkeypatch.setattr(ig_mod, "_token", lambda ctx: "IGAA-secret-token-123")
    _queue([("2026W30-S01", "short", "instagram", "1801")])
    rsps.add(responses.GET, f"{IG}/1801/insights", status=400,
             json={"error": {"code": 190, "type": "OAuthException", "message": "Error validating access token"}})
    m, _ = run(WEEK, sources=["instagram"], cfg=_cfg(), now=NOW)
    res = m["sources"]["instagram"]
    assert res["ok"] is False and "auth instagram" in res["error"] and "IGAA-secret" not in json.dumps(m, ensure_ascii=False)


def test_facebook_social_actions_and_missing_media(root, rsps, monkeypatch):
    monkeypatch.setenv("FACEBOOK_PAGE_TOKEN", "EAAG-page-token-xyz")
    _queue([("2026W30-S01", "short", "facebook", "9001"), ("2026W30-S02", "short", "facebook", "9002")])
    rsps.add(responses.GET, f"{FB}/9001/video_insights", json={"data": [
        {"name": "blue_reels_play_count", "period": "lifetime", "values": [{"value": 1500}]},
        {"name": "post_impressions_unique", "period": "lifetime", "values": [{"value": 1200}]},
        {"name": "post_video_avg_time_watched", "period": "lifetime", "values": [{"value": 8500}]},
        {"name": "post_video_social_actions", "period": "lifetime", "values": [{"value": {"REACTION": 30, "COMMENT": 4, "SHARE": 6}}]},
    ]})
    rsps.add(responses.GET, f"{FB}/9002/video_insights", status=400,
             json={"error": {"code": 100, "message": "Unsupported get request. Object with ID '9002' does not exist"}})
    m, _ = run(WEEK, sources=["facebook"], cfg=_cfg(), now=NOW)
    res = m["sources"]["facebook"]
    assert res["ok"] and res["partial"]
    r = res["rows"][0]
    assert (r["views"], r["reach"], r["likes"], r["comments"], r["shares"]) == (1500, 1200, 30, 4, 6)
    assert r["avg_view_seconds"] == pytest.approx(8.5)
    assert any("2026W30-S02" in w and "조회 실패" in w for w in res["warnings"])


# ---------------------------------------------------------------- 틱톡

def test_tiktok_batches_of_20_and_skips_publish_ids(root, rsps, monkeypatch):
    from studio.publish import tiktok as tt_pub

    monkeypatch.setattr(tt_pub, "access_token", lambda log=None, force_refresh=False: "act.tiktok-token-1")
    ids = [str(7300000000000000000 + i) for i in range(21)]
    _queue([(f"2026W30-S{i + 1:02d}", "short", "tiktok", vid) for i, vid in enumerate(ids)]
           + [("2026W30-S99", "short", "tiktok", "v_pub_file~v2-1.123")])
    bodies = []

    def cb(request):
        body = json.loads(request.body)
        bodies.append(body["filters"]["video_ids"])
        assert request.headers["Authorization"] == "Bearer act.tiktok-token-1"
        assert "view_count" in _qs(request)["fields"]
        vids = [{"id": v, "view_count": 100, "like_count": 5, "comment_count": 1, "share_count": 2}
                for v in body["filters"]["video_ids"] if v != ids[3]]
        return 200, {}, json.dumps({"data": {"videos": vids}, "error": {"code": "ok", "message": ""}})

    rsps.add_callback(responses.POST, TT, callback=cb)
    m, _ = run(WEEK, sources=["tiktok"], cfg=_cfg(), now=NOW)
    res = m["sources"]["tiktok"]
    assert res["ok"] and res["partial"]
    assert [len(b) for b in bodies] == [20, 1]
    assert len(res["rows"]) == 20 and res["rows"][0]["views"] == 100 and res["rows"][0]["lifetime"] is True
    assert any("publish_id" in w and "2026W30-S99" in w for w in res["warnings"])
    assert any("2026W30-S04" in w and "SELF_ONLY" in w for w in res["warnings"])


# ---------------------------------------------------------------- Supabase(클릭·설치·결제)

def test_funnel_paginates_quotes_and_hashes(root, rsps, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SB)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key-abcdef")
    monkeypatch.setattr(funnel_mod, "PAGE_SIZE", 2)
    rc_user = "$RCAnonymousID:abc,1"
    purchases = [
        {"id": 1, "created_at": "2026-07-21T13:00:00+09:00", "event_id": "e1", "event_type": "INITIAL_PURCHASE", "app_user_id": "u1",
         "period_type": "NORMAL", "price_in_usd": 10, "environment": "PRODUCTION", "event_timestamp": "2026-07-21T13:00:00+09:00"},
        {"id": 2, "created_at": "2026-07-22T13:00:00+09:00", "event_id": "e2", "event_type": "RENEWAL", "app_user_id": rc_user,
         "period_type": "NORMAL", "price_in_usd": 10, "environment": "PRODUCTION", "event_timestamp": "2026-07-22T13:00:00+09:00"},
        {"id": 3, "created_at": "2026-07-23T13:00:00+09:00", "event_id": "e3", "event_type": "INITIAL_PURCHASE", "app_user_id": "u1",
         "period_type": "TRIAL", "price_in_usd": 0, "environment": "PRODUCTION", "event_timestamp": "2026-07-23T13:00:00+09:00"},
    ]
    installs = [{"id": 1, "created_at": "2026-07-21T12:00:00+09:00", "app_user_id": "u1", "utm_source": "youtube", "utm_content": "2026W30-S01"}]
    prior = [{"id": 9, "created_at": "2026-06-01T09:00:00+09:00", "app_user_id": rc_user, "utm_source": "youtube", "utm_content": "2026W22-L1"}]
    clicks = [{"id": 1, "created_at": "2026-07-21T11:00:00+09:00", "source": "youtube", "content": "2026W30-S01"}]
    old_clicks = [{"id": 7, "created_at": "2026-05-31T10:00:00+09:00", "source": "youtube", "content": "2026W22-L1"}]
    seen: list[tuple[str, list[tuple[str, str]], str]] = []

    def cb(request):
        table = urlparse(request.url).path.rsplit("/", 1)[-1]
        q = parse_qs(urlparse(request.url).query)
        pairs = [(k, v) for k, vs in q.items() for v in vs]
        seen.append((table, pairs, request.headers.get("Range", "")))
        assert request.headers["apikey"] == "service-role-key-abcdef" and request.headers["Range-Unit"] == "items"
        start, end = (int(x) for x in request.headers["Range"].split("-"))
        if table == "purchases":
            rows = purchases
        elif table == "app_installs":
            rows = prior if "app_user_id" in q else installs
        else:
            rows = old_clicks if "content" in q else clicks
        page = rows[start:end + 1]
        last = start + len(page) - 1
        return 206 if len(rows) > len(page) else 200, {"Content-Range": f"{start}-{last}/{len(rows)}"}, json.dumps(page)

    for t in ("purchases", "app_installs", "link_clicks"):
        rsps.add_callback(responses.GET, f"{SB}/rest/v1/{t}", callback=cb)
    m, _ = run(WEEK, sources=["funnel"], cfg=_cfg(), now=NOW)
    res = m["sources"]["funnel"]
    assert res["ok"], res
    data = res["data"]
    assert len(data["purchases"]) == 3                                         # 2쪽(0-1, 2-2)
    assert [r for t, _, r in seen if t == "purchases"] == ["0-1", "2-3"]
    in_query = next(dict(p)["app_user_id"] for t, p, _ in seen if t == "app_installs" and "app_user_id" in dict(p))
    assert in_query == 'in.("$RCAnonymousID:abc,1")'
    content_q = next(dict(p)["content"] for t, p, _ in seen if t == "link_clicks" and "content" in dict(p))
    assert content_q == 'in.("2026W22-L1")'
    created = [v for t, p, _ in seen if t == "link_clicks" and "content" not in dict(p) for k, v in p if k == "created_at"]
    assert created[0].startswith("gte.2026-07-13T00:00:00")                   # 기간 시작 − 7일
    uids = {r["app_user_id"] for r in data["app_installs"]} | {r["app_user_id"] for r in data["purchases"]}
    assert uids == {hash_uid("u1"), hash_uid(rc_user)} and all(u.startswith("u_") for u in uids)
    assert "abc" not in json.dumps(data)                                       # 원래 ID 는 파일에 남지 않음
    assert len(data["link_clicks"]) == 2


def test_funnel_missing_table_message(root, rsps, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SB)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key-abcdef")
    rsps.add(responses.GET, f"{SB}/rest/v1/purchases", status=404,
             json={"code": "PGRST205", "message": "Could not find the table 'public.purchases' in the schema cache"})
    m, _ = run(WEEK, sources=["funnel"], cfg=_cfg(), now=NOW)
    res = m["sources"]["funnel"]
    assert res["ok"] is False and "마이그레이션" in res["error"] and "service-role-key" not in res["error"]


def test_pg_in_escapes_quotes():
    assert pg_in(['a"b', "c\\d"]) == 'in.("a\\"b","c\\\\d")'


# ---------------------------------------------------------------- dry-run

def test_dry_run_sends_nothing_and_keeps_metrics(root, rsps, monkeypatch, capsys):
    from studio import cliutil
    from studio.cli import main

    cliutil._GROUPS.clear()
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    _queue([("2026W30-S01", "short", "instagram", "1801"), ("2026W30-S01", "short", "tiktok", "7300000000000000001"),
            ("2026W30-S01", "short", "facebook", "9001")])
    (root / "channel.yaml").write_text("app:\n  link_base: https://x/go\n", encoding="utf-8")
    config.load_config.cache_clear()
    assert main(["analytics", "collect", "--week", WEEK]) == 0      # 등록된 응답이 없어 네트워크를 쓰면 실패한다
    assert len(rsps.calls) == 0
    assert load_metrics(WEEK) is None
    dry = json.loads((paths.reports_dir(WEEK) / "metrics.dry-run.json").read_text(encoding="utf-8"))
    assert all(s["dry_run"] for s in dry["sources"].values())
    assert any("SUPABASE_URL" in w for w in dry["sources"]["funnel"]["warnings"])
    out = capsys.readouterr().out
    assert "[dry-run] [youtube] GET https://youtubeanalytics.googleapis.com/v2/reports" in out
    assert "/1801/insights" in out and "video_ids" in out and "/9001/video_insights" in out and "rest/v1/purchases" in out


def test_media_lookback_limits_old_posts(root):
    from studio.analytics.collect import CollectContext
    from studio.analytics.common import load_targets, settings, week_period

    at_old = datetime(2026, 4, 1, 12, 0, tzinfo=paths.KST)
    q = PublishQueue(week="2026-W14", targets=[
        PublishTarget(asset_id="2026W14-S01", asset_kind="short", platform="instagram", publish_at=at_old, status="published", remote_id="1")])
    write_model(paths.content_dir("2026-W14") / "queue.json", q)
    _queue([("2026W30-S01", "short", "instagram", "2"), ("2026W30-S02", "short", "instagram", "dry-run-ig-x")])
    p = week_period(WEEK, NOW)
    targets, warns = load_targets(until=p.end)
    assert {t.remote_id for t in targets} == {"1", "2"} and not warns       # dry-run ID 는 제외
    ctx = CollectContext(WEEK, p, _cfg(), settings(_cfg()), targets)
    assert [t.remote_id for t in ctx.media_targets("instagram")] == ["2"]   # 8주보다 오래된 게시물 제외
    assert p.end - timedelta(weeks=8) > at_old
