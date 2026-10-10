"""앱 팩 내보내기(pack_schema.json 적합성·내부 필드 제외·버전) + Supabase Storage 업로드(리허설·요청 모양)."""

from __future__ import annotations

import hashlib
import json
import re

import pytest
import responses

from studio.bank.schema import SchemaError, pack_schema, validate
from studio.bank.store import editing, load_bank
from test_bank_helpers import LawServer, bank_main, fx, setup_root

ROWS = fx("bank_new_items.json")


@pytest.fixture
def root(tmp_path, monkeypatch):
    return setup_root(tmp_path, monkeypatch)


@pytest.fixture
def verified(root):
    """8문항 add → verify(7 통과·1 반려) → 일부 crosscheck."""
    p = root / "new.json"
    p.write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        LawServer(r)
        assert bank_main(["add", "--file", str(p)]) == 0
        assert bank_main(["verify"]) == 2
    answers = {"ox": lambda it: "O" if it.answer else "X", "mcq": lambda it: "④", "blank": lambda it: "3개월"}
    done = []
    for it in load_bank().all():
        if it.status == "verified" and it.type in answers and it.unit != "중개대상물":   # mcq 하나는 교차확인 안 함
            for _ in range(2):
                assert bank_main(["crosscheck", "--id", it.id, "--answer", answers[it.type](it)]) == 0
            done.append(it.id)
    return root, done


def _read(root, name):
    return json.loads((root / "out" / "bank" / name).read_text(encoding="utf-8"))


def test_export_pack_matches_schema(verified):
    root, done = verified
    assert bank_main(["export"]) == 0
    m = _read(root, "manifest.json")
    validate(m, "#/$defs/manifest")
    validate(m, "#")                                    # 최상위 oneOf 로도 맞다
    # verified + 교차확인(또는 calc 면제)만: ox 4(반려 1 제외)… 문항별로 확인
    pack_ids = {i["id"] for s in m["subjects"] for i in _read(root, s["file"])["items"]}
    calc_ids = {it.id for it in load_bank().all() if it.type == "calc"}
    assert pack_ids == set(done) | calc_ids
    assert m["total"] == len(pack_ids)
    assert m["exam"]["next_exam_date"] == "2026-10-31" and m["exam"]["round"] == 37
    assert m["version"].startswith("20261010-") and m["version"][9:] == m["content_hash"][:8]
    assert m["free_ids"] == sorted(i for i in pack_ids if load_bank().require(i).free)
    for s in m["subjects"]:
        raw = (root / "out" / "bank" / s["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == s["sha256"] and len(raw) == s["bytes"]
        pack = json.loads(raw)
        validate(pack, "#/$defs/subjectPack")
        assert pack["version"] == m["version"] and pack["count"] == len(pack["items"]) == s["count"]
        for item in pack["items"]:
            assert not {"verify_log", "status", "used_in", "created_week"} & set(item)
            assert all(b["badge"].startswith("[") for b in item["basis"])
    assert m["decks"] == []
    brk = _read(root, "BRK.json")["items"]
    direct = next(i for i in brk if i["unit"] == "금지행위")
    assert direct["alerts"] == [{"kind": "upcoming_amendment", "law_name": "공인중개사법",
                                 "article": "제33조 제1항 제6호", "effective_date": "2026-10-20"}]


def test_export_with_jsonschema_library(verified):
    jsonschema = pytest.importorskip("jsonschema")
    root, _ = verified
    bank_main(["export", "--allow-unchecked"])
    schema = pack_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    for f in (root / "out" / "bank").glob("*.json"):
        errors = list(v.iter_errors(json.loads(f.read_text(encoding="utf-8"))))
        assert not errors, (f.name, [e.message for e in errors[:3]])


def test_export_is_deterministic_and_cleans_stale(verified):
    root, _ = verified
    bank_main(["export"])
    v1 = _read(root, "manifest.json")["version"]
    (root / "out" / "bank" / "OLD.json").write_text("{}", encoding="utf-8")
    bank_main(["export"])
    assert _read(root, "manifest.json")["version"] == v1
    assert not (root / "out" / "bank" / "OLD.json").exists()
    with editing() as bank:                             # 내용이 바뀌면 해시가 바뀐다
        it = next(i for i in bank.all() if i.status == "verified" and i.type == "calc")
        it.explanation += " (보충)"
    bank_main(["export"])
    assert _read(root, "manifest.json")["version"] != v1


def test_schema_rejects_bad_items():
    good = {"id": "Q-BRK-0000abcd", "subject": "BRK", "unit": "u", "type": "ox", "stem": "지문입니다.",
            "answer": True, "explanation": "해설입니다.", "trap": "기타", "difficulty": 1, "free": False, "version": 1,
            "updated_at": "2026-10-10T12:00:00+09:00",
            "basis": [{"kind": "law", "law_name": "공인중개사법", "article": "제7조", "effective_date": "2026-01-01",
                       "badge": "[공인중개사법 제7조 · 2026.1.1 시행 기준]"}]}
    validate(good, "#/$defs/item")
    for patch in ({"answer": 1}, {"choices": ["a", "b"]}, {"verify_log": []}, {"trap": "없음"},
                  {"id": "Q-XX-1"}, {"basis": [{"kind": "law", "badge": "[x]"}]}):
        with pytest.raises(SchemaError):
            validate({**good, **patch}, "#/$defs/item")
    mcq = {**good, "type": "mcq", "answer": 2, "choices": ["a", "b", "c"]}
    validate(mcq, "#/$defs/item")
    with pytest.raises(SchemaError):
        validate({**mcq, "answer": True}, "#/$defs/item")


# ---------------------------------------------------------------- 업로드

def test_upload_dry_run_needs_no_keys(verified, capsys, monkeypatch):
    root, _ = verified
    bank_main(["export"])
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    assert bank_main(["upload"]) == 0
    out = capsys.readouterr().out
    v = _read(root, "manifest.json")["version"]
    lines = [ln for ln in out.splitlines() if ln.startswith("[리허설] media/")]
    assert len(lines) == 14
    assert lines[-1].startswith("[리허설] media/bank/latest/manifest.json") and "max-age=300" in lines[-1]
    assert f"media/bank/{v}/BRK.json" in out and "max-age=31536000" in out


def test_upload_posts_in_order(verified, monkeypatch):
    root, _ = verified
    bank_main(["export"])
    monkeypatch.setenv("SUPABASE_URL", "https://abcd.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "eyJtest")
    v = _read(root, "manifest.json")["version"]
    with responses.RequestsMock() as r:
        r.add(responses.POST, re.compile(r"https://abcd\.supabase\.co/storage/v1/object/media/bank/.*"), json={"Key": "ok"})
        assert bank_main(["upload"]) == 0
        urls = [c.request.url for c in r.calls]
        assert len(urls) == 14
        assert urls[0].endswith(f"/bank/{v}/RE.json") and urls[-1].endswith("/bank/latest/manifest.json")
        last = r.calls[-1].request
        assert last.headers["cache-control"] == "max-age=300" and last.headers["x-upsert"] == "true"
        assert last.headers["Authorization"] == "Bearer eyJtest" and last.headers["apikey"] == "eyJtest"
        first = r.calls[0].request
        assert first.headers["cache-control"] == "max-age=31536000"
        assert json.loads(last.body)["version"] == v


def test_upload_refuses_tampered_pack(verified, monkeypatch):
    root, _ = verified
    bank_main(["export"])
    (root / "out" / "bank" / "BRK.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    assert bank_main(["upload"]) == 1


def test_upload_reports_bucket_error(verified, monkeypatch):
    bank_main(["export"])
    monkeypatch.setenv("SUPABASE_URL", "https://abcd.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_x")
    with responses.RequestsMock() as r:
        r.add(responses.POST, re.compile(r"https://abcd\.supabase\.co/.*"), status=404,
              json={"statusCode": "404", "error": "Bucket not found"})
        assert bank_main(["upload"]) == 1
        assert "Authorization" not in r.calls[0].request.headers      # 새 형식 키는 apikey 헤더만


def test_list_ready_json_for_content_modules(verified, capsys):
    root, done = verified
    capsys.readouterr()
    assert bank_main(["list", "--ready", "--subject", "BRK", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in rows} == {i for i in done if i.startswith("Q-BRK-")}
    r0 = rows[0]
    assert r0["crosscheck"]["status"] == "passed" and r0["badges"][0].startswith("[공인중개사법 ")
    assert r0["status"] == "verified" and "verify_log" not in r0
    bank_main(["mark-used", "--asset", "2026W42-S01", "--items", r0["id"]])
    capsys.readouterr()
    bank_main(["list", "--ready", "--unused", "--subject", "BRK", "--json"])
    assert r0["id"] not in {r["id"] for r in json.loads(capsys.readouterr().out)}


def test_verify_skips_retired(verified, capsys):
    root, done = verified
    with editing() as bank:
        bank.require(done[0]).status = "retired"
    capsys.readouterr()
    assert bank_main(["verify", "--id", done[0], "--offline"]) == 0
    assert "retired" in capsys.readouterr().err
    assert load_bank().require(done[0]).status == "retired"


def test_manifest_decks_follow_used_in(verified):
    root, done = verified
    bank_main(["mark-used", "--asset", "2026W42-S03", "--items", done[0], done[1]])
    bank_main(["mark-used", "--asset", "2026W42-B01", "--items", done[1]])
    bank_main(["export"])
    m = _read(root, "manifest.json")
    validate(m, "#/$defs/manifest")
    assert m["decks"] == [
        {"asset_id": "2026W42-B01", "week": "2026-W42", "kind": "blog", "items": [done[1]]},
        {"asset_id": "2026W42-S03", "week": "2026-W42", "kind": "short", "items": sorted([done[0], done[1]])},
    ]
