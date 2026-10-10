"""숫자 정규화(한글 숫자·분수·퍼센트·금액·기간)와 인용문 정규화 비교."""

from __future__ import annotations

import pytest

from studio.verify.normalize import contains, extract_numbers, find_quote, normalize_quote, parse_number


def nums(text: str) -> list[tuple[str, float]]:
    return [(n.kind, round(n.value, 6)) for n in extract_numbers(text)]


@pytest.mark.parametrize("token,value", [
    ("1천500", 1500), ("3억5천만", 350_000_000), ("1,000", 1000), ("0.4", 0.4), ("삼십", 30),
    ("이십일", 21), ("백", 100), ("일천", 1000), ("2억", 200_000_000), ("1만2천", 12_000),
])
def test_parse_number(token, value):
    assert parse_number(token) == pytest.approx(value)


@pytest.mark.parametrize("token", ["사이", "일이", "이사", "", "abc"])
def test_parse_number_rejects_words(token):
    assert parse_number(token) is None


@pytest.mark.parametrize("text,expected", [
    ("삼십일 이내에 신고하여야 한다", [("day", 30)]),          # 옛 법령 표기: 삼십일 = 30일
    ("삼십일일 이내", [("day", 31)]),
    ("삼십일이내", [("day", 30)]),
    ("이십년간 점유", [("year", 20)]),
    ("십일 이내", [("day", 10)]),
    ("100분의 60 이하", [("ratio", 0.6)]),
    ("1천분의 40", [("ratio", 0.04)]),
    ("2분의 1 이상", [("ratio", 0.5)]),
    ("삼분의 이 이상", [("ratio", 0.666667)]),
    ("60% 이하", [("ratio", 0.6)]),
    ("60퍼센트", [("ratio", 0.6)]),
    ("1천500퍼센트 이하", [("ratio", 15.0)]),
    ("0.4%", [("ratio", 0.004)]),
    ("3억5천만원", [("money", 350_000_000)]),
    ("3억 5천만원", [("money", 350_000_000)]),
    ("20만원 이하", [("money", 200_000)]),
    ("1,000원", [("money", 1000)]),
    ("6개월 이내", [("month", 6)]),
    ("6월간", [("month", 6)]),                                 # 민법 옛 표기 '6월' = 6개월
    ("매년 6월 1일", [("md", 601)]),
    ("7월 16일부터 7월 31일까지", [("md", 716), ("md", 731)]),
    ("330㎡ 이하", [("area", 330)]),
    ("330제곱미터", [("area", 330)]),
    ("4미터 이상", [("length", 4)]),
    ("300세대 이상", [("household", 300)]),
    ("1세대 1주택", [("household", 1)]),                       # '1주택'의 '주'는 주(週)가 아니다
    ("2인 이상", [("person", 2)]),
    ("1.5배", [("multiple", 1.5)]),
    ("10년을 넘지 못한다. 1년 미만", [("year", 10), ("year", 1)]),
])
def test_extract_numbers(text, expected):
    assert nums(text) == [(k, pytest.approx(v, rel=1e-5)) for k, v in expected]


@pytest.mark.parametrize("text", [
    "제33조 제1항 제6호", "법 제23조제1항", "제10조의2", "제삼자에 대하여", "제1종 일반주거지역",
    "2026.1.1 시행 기준", "2026년 1월 1일", "2026-01-01", "2026년에", "이월결손금", "구분의 일부",
    "37회 시험", "일일 평균", "이 법은", "일정한 보수",
])
def test_ignored_numbers(text):
    assert extract_numbers(text) == []


def test_units_compare_across_spellings():
    law = extract_numbers("건폐율은 100분의 60 이하로 한다. 6월 내에 행사하여야 한다.")
    assert contains(law, extract_numbers("60%")[0])
    assert contains(law, extract_numbers("6개월")[0])
    assert not contains(law, extract_numbers("70%")[0])
    assert not contains(law, extract_numbers("6년")[0])     # 단위가 다르면 다른 숫자


def test_editorial_notes_are_ignored():
    assert extract_numbers("<개정 2020. 6. 9., 2023. 8. 8.>") == []
    assert extract_numbers("[본조신설 2013. 7. 17.]") == []


# ---------------------------------------------------------------- 인용문

def test_normalize_quote_rules():
    src = "① 임대차는 그 등기(登記)가 없는 경우에도 「주택」의 인도ㆍ주민등록을 마친 때에는 <개정 2023.8.8>"
    n = normalize_quote(src)
    assert n == "임대차는그등기가없는경우에도주택의인도주민등록을마친때에는"
    assert normalize_quote("매매ㆍ교환·임대차") == normalize_quote("매매 교환 임대차")
    assert normalize_quote("“중개”라 함은") == normalize_quote('"중개"라 함은') == normalize_quote("중개라 함은")
    assert normalize_quote("１００분의 ６０") == normalize_quote("100분의 60")


def test_find_quote_exact_and_similar():
    src = "① 임대차는 그 등기(登記)가 없는 경우에도 임차인(賃借人)이 주택의 인도(引渡)와 주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다."
    m = find_quote("임차인이 주택의 인도와 주민등록을 마친 때에는 그 다음 날부터", src)
    assert m.found and m.score == 1.0
    assert "임차인(賃借人)" in m.snippet                      # 원래 표기로 돌려준다
    miss = find_quote("임차인이 주택의 인도와 주민등록을 마친 때에는 그 즉시", src)
    assert not miss.found
    assert 0.7 < miss.score < 1.0
    assert "주민등록" in miss.snippet
    assert not find_quote("", src).found
