"""국가법령정보 Open API 클라이언트 — 응답 해석(픽스처는 실제 응답 저장본의 모양을 따름), 조·항·호·목 추출,
법령명 해석, 캐시(OC 미저장·기한·오프라인), 오류 응답·재시도."""

from __future__ import annotations

import json
from datetime import date

import pytest
import responses

from studio.verify import law_api
from studio.verify.law_api import LawApi, LawApiError, LawApiUnavailable, parse_history
from studio.verify.lawtext import ArticleRef, ArticleRefError, parse_law
from test_bank_helpers import NOW, LawServer, fx, setup_root


@pytest.fixture
def root(tmp_path, monkeypatch):
    return setup_root(tmp_path, monkeypatch)


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


def api(**kw) -> LawApi:
    return LawApi(kw.pop("oc", "testoc"), today=NOW.date(), min_interval=0, **kw)


# ---------------------------------------------------------------- 조문 표기

@pytest.mark.parametrize("text,display,jo", [
    ("제33조 제1항 제6호", "제33조 제1항 제6호", "003300"),
    ("제33조제1항제6호", "제33조 제1항 제6호", "003300"),
    ("제10조의2 제1항", "제10조의2 제1항", "001002"),
    ("제77조 제1항 제1호 가목", "제77조 제1항 제1호 가목", "007700"),
    ("제2조 제1호의2", "제2조 제1호의2", "000200"),
    ("제565조 제1항 단서", "제565조 제1항 단서", "056500"),
    (" 제3조 ", "제3조", "000300"),
])
def test_article_ref(text, display, jo):
    ref = ArticleRef.parse(text)
    assert ref.display() == display and ref.jo_code() == jo


def test_article_ref_appendix_and_errors():
    assert ArticleRef.parse("별표 1").display() == "별표 1"
    assert ArticleRef.parse("[별표 2의3]").appendix_branch == 3
    for bad in ("33조", "제33조 1항", "제조", "제33조 제1항 제6호 가"):
        with pytest.raises(ArticleRefError):
            ArticleRef.parse(bad)


# ---------------------------------------------------------------- 본문 해석

def test_parse_law_service_fixture():
    doc = parse_law(fx("law_service_brokerage.json"))
    assert doc.name == "공인중개사법" and doc.law_id == "001823" and doc.kind == "법률"
    assert doc.effective_date == date(2026, 1, 1)
    assert [a.label for a in doc.articles][:3] == ["제1조", "제2조", "제3조"]   # '전문'(장 제목)은 건너뜀
    assert doc.article(18, 2).title == "중개대상물의 표시ㆍ광고"
    # 항이 하나뿐인 조문(번호 없는 항 객체) — 제2조 제1호
    loc = doc.locate(ArticleRef.parse("제2조 제1호"))
    assert loc.ok and loc.text.startswith("1.") and "알선하는 것을 말한다" in loc.text
    # '제1항'으로 적어도 번호 없는 항을 쓴다
    assert doc.locate(ArticleRef.parse("제3조 제1항 제2호")).text.endswith("토지의 정착물")
    # 항 번호 뒤 공백("① ")도 읽는다
    assert "3년이 지나지 아니한 자" in doc.locate(ArticleRef.parse("제10조 제1항 제4호")).text
    # 항 단위는 아래 호까지 포함
    hang = doc.locate(ArticleRef.parse("제33조 제1항")).text
    assert "직접 거래" in hang and "② " not in hang
    # 못 찾는 경우 이유
    assert "제9항이 없습니다" in doc.locate(ArticleRef.parse("제33조 제9항")).error
    assert "없습니다" in doc.locate(ArticleRef.parse("제99조")).error
    assert "항이 나뉘어" in doc.locate(ArticleRef.parse("제33조 제6호")).error


def test_parse_law_variants():
    data = {"법령": {"기본정보": {"법령명_한글": "가상법", "법령ID": "1", "시행일자": "20260101"},
                    "조문": {"조문단위": {"조문번호": "5", "조문여부": "조문", "조문내용": "제5조 삭제 <2020. 1. 1.>"}},
                    "별표": {"별표단위": [{"별표번호": "0001", "별표가지번호": "00", "별표제목": "건폐율표",
                                         "별표내용": [["1. 주거지역: 100분의 60", "2. 상업지역: 100분의 80"]]}]}}}
    doc = parse_law(data)
    assert doc.article(5).is_deleted()
    assert "삭제된 조문" in doc.locate(ArticleRef.parse("제5조")).error
    ap = doc.locate(ArticleRef.parse("별표 1"))
    assert ap.ok and "100분의 80" in ap.text
    with pytest.raises(Exception):
        parse_law({"LawSearch": {}})


def test_parse_history_tolerant():
    hist = parse_history(fx("law_jo_history.json"))
    assert [h.effective for h in hist] == [date(2014, 7, 28), date(2026, 1, 1)]
    assert hist[-1].changed_at == date(2025, 1, 21) and hist[-1].reason == "조문변경"
    flat = parse_history({"LawSearch": {"law": [{"시행일자": "20240101", "변경사유": "일부개정", "법령일련번호": "1"}]}})
    assert flat[0].effective == date(2024, 1, 1)


# ---------------------------------------------------------------- HTTP·캐시

def test_resolve_exact_name_and_cache(root, rsps):
    server = LawServer(rsps)
    a = api()
    ref = a.resolve("공인중개사법")
    assert (ref.law_id, ref.mst, ref.effective_date) == ("001823", "900001", date(2026, 1, 1))
    assert a.resolve("공인중개사법 시행령").law_id == "003822"
    with pytest.raises(LawApiError, match="정확히 같은"):
        a.resolve("공인중개사")
    # OC 는 요청에는 실리지만 캐시 파일에는 없다
    assert all(r.get("OC") == "testoc" and r.get("type") == "JSON" for r in server.requests)
    files = list((root / "out" / "cache" / "law").rglob("*.json"))
    assert files and all("testoc" not in f.read_text(encoding="utf-8") for f in files)
    n = len(server.requests)
    b = api()                                     # 새 클라이언트도 파일 캐시를 쓴다(7일 안)
    b.resolve("공인중개사법")
    assert len(server.requests) == n


def test_offline_and_refresh(root, rsps):
    server = LawServer(rsps)
    with pytest.raises(LawApiUnavailable, match="캐시"):
        api(oc=None, offline=True).law("001823")
    api().law("001823")
    n = len(server.requests)
    doc = api(oc=None, offline=True).law("001823")
    assert doc.name == "공인중개사법" and len(server.requests) == n
    api(refresh=True).law("001823")
    assert len(server.requests) == n + 1
    with pytest.raises(LawApiUnavailable, match="LAW_API_OC"):
        api(oc=None).law("009999")


def test_upcoming_versions_and_version_body(root, rsps):
    LawServer(rsps)
    a = api()
    ups = a.upcoming("001823")
    assert [(v.mst, v.effective_date) for v in ups] == [("900101", date(2026, 10, 20)), ("900102", date(2027, 6, 1))]
    v = a.law_version(ups[0].mst, ups[0].effective_date)
    assert "제3자를 통하여" in v.locate(ArticleRef.parse("제33조 제1항 제6호")).text
    assert a.upcoming("999999") == []


def test_precedent_lookup(root, rsps):
    LawServer(rsps)
    a = api()
    p = a.find_precedent("2019다 299999")
    assert p and p.prec_id == "777001" and p.decided_at == date(2020, 5, 14)
    assert "소멸시효에 걸리지 않는다" in a.precedent_text(p.prec_id)
    assert "<br/>" not in a.precedent_text(p.prec_id)
    assert a.find_precedent("2020다1") is None


def test_error_response_and_retry(root, rsps, monkeypatch):
    url = "https://www.law.go.kr/DRF/lawSearch.do"
    rsps.add(responses.GET, url, json={"result": "사용자 정보 검증에 실패하였습니다.", "msg": "IP 를 등록하세요"})
    with pytest.raises(LawApiUnavailable, match="검증"):
        api().search_laws("민법")
    rsps.replace(responses.GET, url, body="<html>점검 중</html>", status=200)
    with pytest.raises(LawApiError, match="JSON"):
        api().search_laws("주택임대차보호법")
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return 503, {}, "busy"
        return 200, {}, json.dumps({"LawSearch": {"totalCnt": "0"}})

    rsps.remove(responses.GET, url)
    rsps.add_callback(responses.GET, url, callback=flaky)
    assert api().search_laws("농지법") == []
    assert calls["n"] == 3


def test_decree_lookup(root, rsps):
    LawServer(rsps)
    a = api()
    dec = a.decree("공인중개사법")
    assert dec and dec.name == "공인중개사법 시행령" and dec.article(20)
    assert a.decree("공인중개사법 시행령") is None


def test_history_endpoint(root, rsps):
    server = LawServer(rsps)
    hist = api().article_history("001823", "000700")
    assert hist[-1].effective == date(2026, 1, 1)
    assert server.count(target="lsJoHstInf", ID="001823", JO="000700") == 1


def test_base_url_override(root, rsps, monkeypatch):
    monkeypatch.setenv("LAW_API_BASE", "https://mirror.example/DRF/")
    rsps.add(responses.GET, "https://mirror.example/DRF/lawSearch.do", json={"LawSearch": {"totalCnt": "0"}})
    assert LawApi.from_env().search_laws("x") == []
    assert law_api.LAW_API_BASE.startswith("https://www.law.go.kr")
