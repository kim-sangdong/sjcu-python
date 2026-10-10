"""watch-laws — 인용 조문 개정(본문 해시 변경·삭제·시험 전 시행 예정·이력)을 감지해 retired 로 바꾸고 기록을 남긴다."""

from __future__ import annotations

import json
from datetime import date

import pytest
import responses

from studio.bank.models import NewItem
from studio.bank.store import editing, load_bank
from test_bank_helpers import NOW, LawServer, bank_main, fx, setup_root

ROWS = fx("bank_new_items.json")


@pytest.fixture
def root(tmp_path, monkeypatch):
    return setup_root(tmp_path, monkeypatch)


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        yield r


def _setup(root, rsps, rows=ROWS):
    server = LawServer(rsps)
    p = root / "new.json"
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    bank_main(["add", "--file", str(p)])
    bank_main(["verify"])
    return server


def _by_unit(unit: str):
    return next(it for it in load_bank().all() if it.unit == unit)


def test_watch_retires_changed_and_upcoming(root, rsps, capsys):
    server = _setup(root, rsps)
    lend = _by_unit("자격증 대여 금지")                         # 제7조 제1항
    direct = _by_unit("금지행위")                               # 제33조 제1항 제6호 (10-20 개정 예정)
    disq = _by_unit("등록의 결격사유")                          # 제10조 — 바뀌지 않음
    with editing() as bank:
        bank.require(lend.id).used_in = ["2026W42-S03", "2026W42-B01"]
    server.body = "law_service_brokerage_amended.json"         # 현행 제7조 제1항이 바뀐 상황
    capsys.readouterr()
    assert bank_main(["watch-laws"]) == 0
    out = capsys.readouterr().out
    bank = load_bank()
    assert bank.require(lend.id).status == "retired"
    assert bank.require(direct.id).status == "retired"
    assert bank.require(disq.id).status == "verified"
    assert sum(1 for it in bank.all() if it.status == "retired") == 2
    log = [e for e in bank.require(lend.id).verify_log if e.check == "watch"][-1]
    assert "조문 내용이 바뀌었습니다" in log.detail
    assert "retired 2문항" in out
    md = (root / "content" / "bank" / "law_changes" / "2026-10-10.md").read_text(encoding="utf-8")
    assert "공인중개사법 제7조 제1항 — 조문 내용 변경" in md
    assert "제33조 제1항 제6호 — 시험 전 시행 예정 개정" in md
    assert "2026W42-S03 (쇼츠)" in md and "2026W42-B01 (블로그)" in md and "블로그 상단 배너" in md
    js = json.loads((root / "content" / "bank" / "law_changes" / "2026-10-10.json").read_text(encoding="utf-8"))
    assert js["retired"] == sorted([lend.id, direct.id])
    assert {c["asset_id"] for c in js["corrections"]} == {"2026W42-S03", "2026W42-B01"}
    # 바뀐 조문은 다시 받는다(캐시 무시)
    assert server.count(target="law", ID="001823") >= 2


def test_watch_after_exam_amendment_is_reference_only(root, rsps):
    _setup(root, rsps, rows=[ROWS[0], ROWS[2]])                # 제7조·제3조 문항만
    assert bank_main(["watch-laws"]) == 0
    bank = load_bank()
    assert {it.status for it in bank.all()} == {"verified"}
    js = json.loads((root / "content" / "bank" / "law_changes" / "2026-10-10.json").read_text(encoding="utf-8"))
    kinds = {(c["article"], c["kind"]) for c in js["changes"]}
    assert ("제7조 제1항", "upcoming_after_exam") in kinds    # 2027-06-01 개정이 제7조 제2항을 바꿈(시험 뒤)
    assert js["retired"] == []


def test_watch_detects_deleted_article(root, rsps):
    row = {**ROWS[0], "unit": "표시광고", "stem": "개업공인중개사는 중개대상물 표시·광고에 중개보조원에 관한 사항을 명시해서는 안 된다.",
           "explanation": "제18조의2 제1항은 중개보조원에 관한 사항의 명시를 금지한다.",
           "basis": [{"kind": "law", "law_name": "공인중개사법", "article": "제18조의2 제1항", "effective_date": "2026-01-01",
                      "quote": "중개보조원에 관한 사항은 명시해서는 아니 된다"}]}
    server = _setup(root, rsps, rows=[row])
    assert _by_unit("표시광고").status == "verified"
    server.body = "law_service_brokerage_amended.json"
    assert bank_main(["watch-laws"]) == 0
    it = _by_unit("표시광고")
    assert it.status == "retired"
    assert "삭제" in [e for e in it.verify_log if e.check == "watch"][-1].detail


def test_item_verified_against_upcoming_version_is_not_retired(root, rsps):
    row = {**ROWS[4], "basis": [{**ROWS[4]["basis"][0], "effective_date": "2026-10-20",
                                 "quote": "거래당사자 쌍방을 대리하는 행위(제3자를 통하여 하는 경우를 포함한다)"}]}
    _setup(root, rsps, rows=[row])
    assert _by_unit("금지행위").status == "verified"
    assert bank_main(["watch-laws"]) == 0
    assert _by_unit("금지행위").status == "verified"


def test_history_fallback_without_hash(root, rsps):
    LawServer(rsps)
    it = NewItem.model_validate({**ROWS[0], "basis": [{**ROWS[0]["basis"][0], "effective_date": "2025-06-01"}]}) \
        .to_item("Q-BRK-0000beef", now=NOW)
    it.status = "verified"                                      # 손으로 넣은 verified(해시 기록 없음)
    with editing() as bank:
        bank.put(it)
    assert bank_main(["watch-laws"]) == 0
    after = load_bank().require("Q-BRK-0000beef")
    assert after.status == "retired"
    assert "이후 조문 변경" in [e for e in after.verify_log if e.check == "watch"][-1].detail


def test_watch_dry_run_and_no_key(root, rsps, monkeypatch):
    server = _setup(root, rsps)
    server.body = "law_service_brokerage_amended.json"
    assert bank_main(["watch-laws", "--dry-run"]) == 0
    assert all(it.status != "retired" for it in load_bank().all())
    assert not (root / "content" / "bank" / "law_changes").exists()
    monkeypatch.delenv("LAW_API_OC")
    assert bank_main(["watch-laws"]) == 3


def test_watch_skips_item_changed_meanwhile(root, rsps):
    from studio.bank.store import load_bank as lb
    from studio.bank.watch import apply_retirements, watch
    from studio.verify.law_api import LawApi

    server = _setup(root, rsps, rows=[ROWS[0]])
    server.body = "law_service_brokerage_amended.json"
    snap = lb()
    res = watch(snap, LawApi("oc", today=NOW.date(), min_interval=0), exam_date=date(2026, 10, 31), today=NOW.date())
    assert len(res.retired) == 1
    with editing() as bank:                                     # 감지 뒤 누군가 문항을 고쳤다(version 2)
        bank.require(res.retired[0]).version = 2
    with editing() as bank:
        assert apply_retirements(bank, res, snap) == []
