"""문제은행 명령 — python -m studio bank <하위명령>  (등록 전에는 python -m studio.bank <하위명령>)

  add --file new.json [--dry-run]       새 문항 검사·id 부여·draft 로 추가(중복·근거 누락·금지 표현 거부)
  update --file fixed.json              기존 문항 내용 고치기(version+1, draft 로 돌아감)
  verify [--status draft] [--id Q-..] [--subject BRK] [--offline] [--refresh]
                                        법령 원문 대조 → verified / rejected (API 를 못 쓰면 보류)
  crosscheck --id Q-.. --answer ③ --by claude [--note ..]
                                        정답을 보지 않고 따로 푼 답 기록. 다르면 rejected
  stats [--json]                        과목·단원·유형·상태·함정 분포, 주차별 신규
  list [--ready] [--status verified] [--subject ..] [--type ox] [--unit ..] [--unused] [--json]
                                        --ready = verified + 독립 정답 확인 통과(원고에 쓸 수 있는 문항)
  show --id Q-.. [--blind]              문항 보기(--blind: 정답·해설 없이 — crosscheck 풀이용)
  export [--out out/bank] [--allow-unchecked]
  upload [--dir out/bank] [--dry-run]   Supabase Storage media/bank/<version>/ 와 bank/latest/
  watch-laws [--offline] [--dry-run]    인용 법령 개정 감지 → retired + content/bank/law_changes/<날짜>.md
  mark-used --asset 2026W42-S03 --items Q-.. [Q-..]
  law --name 공인중개사법 --article 제33조 [--decree] [--offline] [--refresh] [--json]
종료 코드: 0 정상 · 1 입력·파일 오류 · 2 거부·반려 있음 · 3 설정(키) 필요
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from studio import paths
from studio.bank.models import now as bank_now
from studio.cliutil import group
from studio.config import load_config

ASSET_ID_RE = re.compile(r"^\d{4}W\d{2}-[LSCB]\d{1,2}$")


def _ids(values: list[str] | None) -> list[str]:
    return [x for v in (values or []) for x in re.split(r"[,\s]+", v) if x]


def _err(msg: str) -> int:
    print(f"[오류] {msg}", file=sys.stderr)
    return 1


def _today() -> date:
    return paths.now_kst().date()


def _validation_msg(e: ValidationError) -> str:
    parts = []
    for x in e.errors()[:4]:
        loc = ".".join(str(p) for p in x.get("loc", ()))
        parts.append(f"{loc}: {x.get('msg')}" if loc else str(x.get("msg")))
    return "; ".join(parts)


def _pad(text: str, width: int) -> str:
    """한글(전각) 두 칸 너비를 고려한 왼쪽 정렬."""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - w)


def _load_rows(path: str) -> list[Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("파일은 문항 목록(JSON 배열) 또는 {\"items\": [...]} 이어야 합니다")
    return data


# ---------------------------------------------------------------- add / update

def cmd_add(args: argparse.Namespace) -> int:
    from studio.bank.dedupe import DupIndex
    from studio.bank.exam import bank_cfg
    from studio.bank.models import ID_RE, NewItem, make_id
    from studio.bank.store import BankError, editing
    from studio.verify.checks import ItemReport, offline_checks

    try:
        rows = _load_rows(args.file)
    except (OSError, ValueError) as e:
        return _err(f"{args.file}: {e}")
    cfg = load_config()
    bcfg = bank_cfg(cfg)
    added: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    now = bank_now()
    try:
        with editing() as bank:
            idx = DupIndex(bcfg.dup_threshold)
            idx.extend((it.id, it.stem) for it in bank.all() if it.status not in ("retired", "rejected"))
            for n, row in enumerate(rows, 1):
                try:
                    new = NewItem.model_validate(row)
                    if new.id:
                        if not ID_RE.match(new.id):
                            raise ValueError(f"id 형식 오류: {new.id}")
                        if bank.get(new.id):
                            raise ValueError(f"이미 있는 id 입니다({new.id}) — 고치려면 bank update")
                        item_id = new.id
                    else:
                        salt = 0
                        item_id = make_id(new.subject, new.stem)
                        while bank.get(item_id) or any(a["id"] == item_id for a in added):
                            salt += 1
                            item_id = make_id(new.subject, new.stem, salt)
                    item = new.to_item(item_id, now=now)
                except ValidationError as e:
                    rejected.append({"row": n, "reasons": [_validation_msg(e)]})
                    continue
                except ValueError as e:
                    rejected.append({"row": n, "reasons": [str(e)]})
                    continue
                rep = ItemReport(item.id, item.version)
                offline_checks(item, rep, cfg.rules.banned_phrases, cfg.rules.caution_phrases)
                reasons = [o.line() for o in rep.failures()]
                dup = idx.duplicate_of(item.stem)
                if dup:
                    reasons.append(f"중복: {dup.other_id} 와 지문 유사도 {dup.score:.2f} (기준 {bcfg.dup_threshold})")
                if reasons:
                    rejected.append({"row": n, "reasons": reasons})
                    continue
                warns = [o.line() for o in rep.outcomes if o.status == "warn"]
                added.append({"row": n, "id": item.id, "subject": item.subject, "type": item.type, "warnings": warns})
                idx.add(item.id, item.stem)
                if not args.dry_run:
                    bank.put(item)
    except BankError as e:
        return _err(str(e))
    if args.json:
        print(json.dumps({"added": added, "rejected": rejected, "dry_run": args.dry_run}, ensure_ascii=False, indent=2))
    else:
        for a in added:
            print(f"[{'추가 예정' if args.dry_run else '추가'}] {a['id']} ({a['type']})")
            for w in a["warnings"]:
                print(f"    {w}")
        for r in rejected:
            print(f"[거부] 입력 {r['row']}번째")
            for why in r["reasons"]:
                print(f"    - {why}")
        print(f"추가 {len(added)} · 거부 {len(rejected)}{' (dry-run: 저장 안 함)' if args.dry_run else ''}")
    return 2 if rejected else 0


def cmd_update(args: argparse.Namespace) -> int:
    from studio.bank.dedupe import DupIndex
    from studio.bank.exam import bank_cfg
    from studio.bank.models import BankItem, NewItem, subject_code
    from studio.bank.store import BankError, editing
    from studio.verify.checks import ItemReport, offline_checks

    try:
        rows = _load_rows(args.file)
    except (OSError, ValueError) as e:
        return _err(f"{args.file}: {e}")
    cfg = load_config()
    bcfg = bank_cfg(cfg)
    now = bank_now()
    bad = 0
    try:
        with editing() as bank:
            idx = DupIndex(bcfg.dup_threshold)
            idx.extend((it.id, it.stem) for it in bank.all() if it.status not in ("retired", "rejected"))
            for n, row in enumerate(rows, 1):
                try:
                    new = NewItem.model_validate(row)
                    if not new.id:
                        raise ValueError("update 에는 id 가 필요합니다")
                    old = bank.require(new.id)
                    if subject_code(new.subject) != old.subject:
                        raise ValueError("과목은 바꿀 수 없습니다(새 문항으로 add)")
                    data = old.model_dump()
                    data.update(unit=new.unit, type=new.type, stem=new.stem, choices=new.choices, answer=new.answer,
                                explanation=new.explanation, trap=new.trap,
                                basis=[b.model_dump() for b in new.basis], difficulty=new.difficulty, free=new.free,
                                status="draft", version=old.version + 1, updated_at=now)
                    item = BankItem.model_validate(data)
                except (ValidationError, ValueError, BankError) as e:
                    msg = _validation_msg(e) if isinstance(e, ValidationError) else str(e)
                    print(f"[거부] 입력 {n}번째: {msg}")
                    bad += 1
                    continue
                rep = ItemReport(item.id, item.version)
                offline_checks(item, rep, cfg.rules.banned_phrases, cfg.rules.caution_phrases)
                reasons = [o.line() for o in rep.failures()]
                dup = idx.duplicate_of(item.stem, exclude=item.id)
                if dup:
                    reasons.append(f"중복: {dup.other_id} 와 지문 유사도 {dup.score:.2f}")
                if reasons:
                    print(f"[거부] {item.id}")
                    for r in reasons:
                        print(f"    - {r}")
                    bad += 1
                    continue
                item.log("update", True, f"v{old.version} → v{item.version} ({old.status} → draft)", at=now)
                bank.put(item)
                print(f"[수정] {item.id} v{item.version} (draft — verify·crosscheck 다시)")
    except BankError as e:
        return _err(str(e))
    return 2 if bad else 0


# ---------------------------------------------------------------- verify / crosscheck

def _verify_report_dir() -> Path:
    return paths.OUT / "bank-verify"


def cmd_verify(args: argparse.Namespace) -> int:
    from studio.bank.exam import bank_cfg, exam_cfg
    from studio.bank.store import BankError, editing, load_bank
    from studio.models import write_json
    from studio.verify.checks import Verifier, summarize
    from studio.verify.law_api import LawApi
    from studio.verify.runner import apply_report

    cfg = load_config()
    exam, bcfg = exam_cfg(cfg), bank_cfg(cfg)
    api = LawApi.from_env(offline=args.offline, refresh=args.refresh)
    if not api.available:
        print("[설정 필요] 환경변수 LAW_API_OC 가 없어 원문 대조(b·c·d)를 보류합니다. 오프라인 검사(a·e·f·g)만 합니다.",
              file=sys.stderr)
    try:
        snap = load_bank()
        items = snap.select(ids=_ids(args.id), statuses=None if args.id else [args.status],
                            subjects=_ids(args.subject))
    except BankError as e:
        return _err(str(e))
    retired = [it.id for it in items if it.status == "retired"]
    if retired:
        print(f"[건너뜀] retired 문항은 `bank update` 로 고친 뒤 검증합니다: {', '.join(retired)}", file=sys.stderr)
        items = [it for it in items if it.status != "retired"]
    if not items:
        print("검증할 문항이 없습니다.")
        return 0
    ver = Verifier(api, exam_date=exam.next_exam_date, today=_today(), banned=cfg.rules.banned_phrases,
                   caution=cfg.rules.caution_phrases, crosscheck_required=bcfg.crosscheck_required)
    reports = []
    for it in items:
        reports.append(ver.run(it.model_copy(deep=True)))
    now = bank_now()
    counts: Counter[str] = Counter()
    out_rows = []
    with editing() as bank:
        for rep in reports:
            it = bank.get(rep.item_id)
            if it is None or it.version != rep.version:
                counts["skipped"] += 1
                out_rows.append({**rep.as_dict(), "status_after": "skipped(검증 중 문항이 바뀜)"})
                continue
            before = it.status
            after = apply_report(it, rep, now)
            counts[rep.verdict] += 1
            out_rows.append({**rep.as_dict(), "status_before": before, "status_after": after})
    stamp = now.strftime("%Y%m%dT%H%M%S")
    payload = {"generated_at": now.isoformat(timespec="seconds"), "exam_date": exam.next_exam_date.isoformat(),
               "api_calls": api.calls, "offline": args.offline, "counts": dict(counts), "items": out_rows}
    d = _verify_report_dir()
    write_json(d / f"{stamp}.json", payload)
    write_json(d / "latest.json", payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        ko = {"verified": "통과", "rejected": "반려", "pending": "보류"}
        for rep in reports:
            if rep.verdict == "verified" and not args.verbose:
                continue
            print(f"[{ko[rep.verdict]}] {rep.item_id}")
            for line in summarize(rep)[:8]:
                print(f"    {line}")
        print(f"검증 {len(reports)}문항: 통과 {counts['verified']} · 반려 {counts['rejected']} · 보류 {counts['pending']}"
              + (f" · 건너뜀 {counts['skipped']}" if counts["skipped"] else "") + f" (API 호출 {api.calls}회)")
        print(f"결과: {(d / 'latest.json').relative_to(paths.ROOT) if (d / 'latest.json').is_relative_to(paths.ROOT) else d / 'latest.json'}")
        if counts["verified"]:
            print(f"다음: verified 문항마다 정답을 보지 않고 두 번 풀어 `bank crosscheck` 로 기록(필요 {bcfg.crosscheck_required}회).")
    if counts["rejected"]:
        return 2
    if counts["pending"]:
        return 3          # 키 없음·API 장애 — 문항 잘못이 아니라 판정 보류
    return 0


def cmd_crosscheck(args: argparse.Namespace) -> int:
    from studio.bank.exam import bank_cfg
    from studio.bank.store import BankError, editing
    from studio.verify.runner import AnswerParseError, record_crosscheck

    bcfg = bank_cfg(load_config())
    try:
        with editing() as bank:
            it = bank.require(args.id)
            if it.status in ("retired",):
                return _err(f"{it.id} 는 retired 문항입니다")
            res = record_crosscheck(it, args.answer, args.by, args.note or "", required=bcfg.crosscheck_required)
    except AnswerParseError as e:
        return _err(str(e))
    except BankError as e:
        return _err(str(e))
    st = res["state"]
    if res["ok"]:
        print(f"[일치] {args.id} — 독립 정답 확인 {st['done']}/{st['required']} ({st['status']})")
        return 0
    print(f"[불일치] {args.id} — 답 {args.answer!r} 가 저장된 정답과 달라 rejected 로 바꿨습니다. 문항을 고쳐 bank update 하세요.")
    return 2


# ---------------------------------------------------------------- 조회

def _stats(bank: Any, weekly_target: int, required: int) -> dict[str, Any]:
    from studio.bank.models import SUBJECTS
    from studio.verify.runner import crosscheck_ok

    items = bank.all()
    this_week = paths.week_of(_today())
    weeks = Counter(it.created_week for it in items)
    recent = []
    w = this_week
    for _ in range(4):
        recent.append({"week": w, "new": weeks.get(w, 0)})
        w = paths.previous_week(w)
    by_subject = {c: dict(Counter(it.status for it in items if it.subject == c)) for c in SUBJECTS}
    return {
        "total": len(items),
        "by_status": dict(Counter(it.status for it in items)),
        "by_subject": by_subject,
        "by_unit": {f"{it_s}·{u}": n for (it_s, u), n in sorted(Counter((it.subject, it.unit) for it in items).items())},
        "by_type": dict(Counter(it.type for it in items)),
        "by_trap": dict(Counter(it.trap for it in items).most_common()),
        "free": sum(1 for it in items if it.free and it.status == "verified"),
        "verified_ready": sum(1 for it in items if it.status == "verified" and crosscheck_ok(it, required)),
        "crosscheck_pending": sum(1 for it in items if it.status == "verified" and not crosscheck_ok(it, required)),
        "used": sum(1 for it in items if it.used_in),
        "weeks": recent,
        "weekly_target": weekly_target,
        "this_week": this_week,
    }


def cmd_stats(args: argparse.Namespace) -> int:
    from studio.bank.exam import bank_cfg
    from studio.bank.models import SUBJECT_SHORT, SUBJECTS
    from studio.bank.store import BankError, load_bank

    bcfg = bank_cfg(load_config())
    try:
        bank = load_bank()
    except BankError as e:
        return _err(str(e))
    s = _stats(bank, bcfg.weekly_target, bcfg.crosscheck_required)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    st = ("draft", "verified", "rejected", "retired")
    print(f"문항 {s['total']}개 — " + " · ".join(f"{k} {s['by_status'].get(k, 0)}" for k in st)
          + f" | 앱 팩 대상 {s['verified_ready']} (교차확인 대기 {s['crosscheck_pending']}) · 무료 {s['free']} · 콘텐츠 사용 {s['used']}")
    print("\n" + _pad("과목", 12) + "".join(f"{k:>9}" for k in st))
    for c in SUBJECTS:
        row = s["by_subject"][c]
        print(_pad(f"{c} {SUBJECT_SHORT[c]}", 12) + "".join(f"{row.get(k, 0):>9}" for k in st))
    print("\n유형: " + " · ".join(f"{k} {v}" for k, v in sorted(s["by_type"].items())))
    total = max(1, s["total"])
    print("함정: " + " · ".join(f"{k} {v}({v * 100 // total}%)" for k, v in s["by_trap"].items()))
    print("단원: " + " · ".join(f"{k} {v}" for k, v in s["by_unit"].items()))
    print("주차별 신규(목표 주 {0}): ".format(s["weekly_target"])
          + " · ".join(f"{w['week']} {w['new']}" for w in s["weeks"]))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from studio.bank.exam import bank_cfg
    from studio.bank.export import item_alerts, public_item
    from studio.bank.store import BankError, load_bank
    from studio.verify.runner import crosscheck_ok, crosscheck_state

    required = bank_cfg(load_config()).crosscheck_required
    try:
        bank = load_bank()
        statuses = ["verified"] if args.ready else _ids(args.status)
        items = bank.select(statuses=statuses, subjects=_ids(args.subject), types=_ids(args.type))
    except BankError as e:
        return _err(str(e))
    if args.ready:
        items = [i for i in items if crosscheck_ok(i, required)]
    if args.unused:
        items = [i for i in items if not i.used_in]
    if args.unit:
        items = [i for i in items if args.unit in i.unit]
    if args.limit:
        items = items[: args.limit]
    if args.json:
        print(json.dumps([{**public_item(i), "status": i.status, "used_in": i.used_in, "badges": i.badges(),
                           "crosscheck": crosscheck_state(i, required), "alerts": item_alerts(i)}
                          for i in items], ensure_ascii=False, indent=2))
        return 0
    for i in items:
        print(f"{i.id}  {i.status:<8} {i.type:<5} {_pad(i.unit[:10], 20)} {i.stem[:46]}")
    print(f"{len(items)}문항")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from studio.bank.store import BankError, load_bank
    from studio.verify.normalize import CIRCLED

    try:
        it = load_bank().require(args.id)
    except BankError as e:
        return _err(str(e))
    if args.blind:
        circ = {v: k for k, v in CIRCLED.items()}
        print(f"{it.id} [{it.type}] {it.unit}")
        print(it.stem)
        for n, c in enumerate(it.choices or [], 1):
            print(f"  {circ[n] if it.type == 'mcq' else str(n - 1) + '.'} {c}")
        hint = {"ox": "O 또는 X", "mcq": "①~⑤", "blank": "빈칸 낱말", "order": "올바른 순서(예: 2,0,1,3 — 0부터)",
                "calc": "숫자"}[it.type]
        print(f"\n답 형식: {hint} → python -m studio bank crosscheck --id {it.id} --answer <답> --by claude")
        return 0
    print(json.dumps(it.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- export / upload

def cmd_export(args: argparse.Namespace) -> int:
    from studio.bank.exam import bank_cfg, disclaimer, exam_cfg
    from studio.bank.export import build_pack
    from studio.bank.schema import SchemaError
    from studio.bank.store import BankError, load_bank

    cfg = load_config()
    out = Path(args.out)
    if not out.is_absolute():
        out = paths.ROOT / out
    try:
        res = build_pack(load_bank(), exam_cfg(cfg), bank_cfg(cfg), disclaimer(cfg), out,
                         require_crosscheck=not args.allow_unchecked)
    except (BankError, SchemaError) as e:
        return _err(f"내보내기 실패: {e}")
    print(f"팩 {res.version}: {res.total}문항 (" + " · ".join(f"{k} {v}" for k, v in res.per_subject.items())
          + f") · 무료 {len(res.free_ids)}")
    for why, ids in res.skipped.items():
        print(f"  제외 {len(ids)} — {why}")
    print(f"출력: {out.relative_to(paths.ROOT) if out.is_relative_to(paths.ROOT) else out}/manifest.json")
    if res.total == 0:
        print("  (앱 팩이 비어 있습니다 — verify 와 crosscheck 를 먼저 하세요)")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    from studio.bank.upload import UploadError, env_dry_run, plan, public_url, upload
    from studio.config import env

    cfg = load_config()
    d = Path(args.dir)
    if not d.is_absolute():
        d = paths.ROOT / d
    try:
        version, items = plan(d)
    except (UploadError, ValueError) as e:
        return _err(str(e))
    bucket = cfg.storage.bucket
    dry = args.dry_run or env_dry_run()
    base = env("SUPABASE_URL", required=False) or ""
    for u in items:
        print(f"{'[리허설] ' if dry else ''}{bucket}/{u.path}  ({u.size:,}B, max-age={u.max_age})")
    if dry:
        print(f"리허설: {len(items)}개 파일 — 실제로 올리지 않았습니다.")
        return 0
    url = str(env("SUPABASE_URL"))
    key = str(env("SUPABASE_SERVICE_ROLE_KEY"))
    try:
        done = upload(items, url=url, key=key, bucket=bucket)
    except UploadError as e:
        return _err(str(e))
    print(f"업로드 {len(done)}개 · 버전 {version}")
    print(f"앱 manifest: {public_url(base or url, bucket, 'bank/latest/manifest.json')}")
    return 0


# ---------------------------------------------------------------- watch / mark-used / law

def cmd_watch(args: argparse.Namespace) -> int:
    from studio.bank.exam import exam_cfg
    from studio.bank.store import BankError, editing, load_bank
    from studio.bank.watch import apply_retirements, watch, write_report
    from studio.verify.law_api import LawApi

    cfg = load_config()
    exam = exam_cfg(cfg)
    api = LawApi.from_env(offline=args.offline, refresh=not args.offline)
    if not api.available:
        print("[설정 필요] 환경변수 LAW_API_OC 가 없습니다(open.law.go.kr 이메일 ID).", file=sys.stderr)
        return 3
    day = date.fromisoformat(args.date) if args.date else _today()
    try:
        snap = load_bank()
    except BankError as e:
        return _err(str(e))
    res = watch(snap, api, exam_date=exam.next_exam_date, today=_today())
    if res.unavailable:
        print(f"[보류] 법령 API 를 쓸 수 없어 개정 감지를 하지 못했습니다: {res.unavailable}", file=sys.stderr)
        return 3
    md = None
    if not args.dry_run and (res.retired or res.changes):
        with editing() as bank:
            apply_retirements(bank, res, snap)
            md, _ = write_report(res, bank, day=day, exam_name=exam.name, exam_round=exam.round,
                                 exam_date=exam.next_exam_date)
    print(f"법령 {len(res.checked_laws)}개 확인 · 변경 {len(res.changes)}건 · retired {len(res.retired)}문항"
          + (" (dry-run: 상태 안 바꿈)" if args.dry_run and res.retired else ""))
    for c in res.changes:
        print(f"  - {c.law_name} {c.article}: {c.detail} → {', '.join(c.items)}")
    for e in res.errors:
        print(f"  ! {e}")
    if md:
        print(f"기록: {md.relative_to(paths.ROOT) if md.is_relative_to(paths.ROOT) else md}")
    return 0


def cmd_mark_used(args: argparse.Namespace) -> int:
    from studio.bank.exam import bank_cfg
    from studio.bank.store import BankError, editing
    from studio.verify.runner import crosscheck_ok

    if not ASSET_ID_RE.match(args.asset):
        return _err(f"콘텐츠 ID 형식이 아닙니다: {args.asset} (예: 2026W42-S03)")
    ids = _ids(args.items)
    if not ids:
        return _err("--items 가 비었습니다")
    required = bank_cfg(load_config()).crosscheck_required
    warn = 0
    try:
        with editing() as bank:
            items = [bank.require(i) for i in ids]
            blocked = [i.id for i in items if i.status != "verified"]
            if blocked and not args.force:
                return _err(f"verified 가 아닌 문항은 콘텐츠에 쓰지 않습니다: {', '.join(blocked)} (--force 로 강제)")
            for it in items:
                if args.asset not in it.used_in:
                    it.used_in = sorted({*it.used_in, args.asset})
                if not crosscheck_ok(it, required):
                    warn += 1
                    print(f"  ! {it.id}: 독립 정답 확인이 끝나지 않았습니다")
    except BankError as e:
        return _err(str(e))
    print(f"{args.asset} ← {len(ids)}문항 기록" + (f" (교차확인 대기 {warn})" if warn else ""))
    return 0


def cmd_law(args: argparse.Namespace) -> int:
    from studio.bank.models import Basis
    from studio.verify.law_api import LawApi, LawApiError, LawApiUnavailable
    from studio.verify.lawtext import ArticleRef, ArticleRefError

    api = LawApi.from_env(offline=args.offline, refresh=args.refresh)
    if not api.available:
        print("[설정 필요] 환경변수 LAW_API_OC 가 없습니다(open.law.go.kr 이메일 ID).", file=sys.stderr)
        return 3
    name = args.name + (" 시행령" if args.decree and not args.name.endswith("시행령") else "")
    try:
        _, doc = api.law_by_name(name)
        ups = api.upcoming(doc.law_id)
    except LawApiUnavailable as e:
        print(f"[보류] {e}", file=sys.stderr)
        return 3
    except LawApiError as e:
        return _err(str(e))
    head = f"{doc.name} (법령ID {doc.law_id}, 현행 시행 {doc.effective_date})"
    if not args.article:
        if args.json:
            print(json.dumps({"law_name": doc.name, "law_id": doc.law_id,
                              "effective_date": str(doc.effective_date),
                              "articles": [{"article": a.label, "title": a.title} for a in doc.articles]},
                             ensure_ascii=False, indent=2))
            return 0
        print(head)
        for a in doc.articles:
            print(f"  {a.label} {a.title}")
        return 0
    try:
        ref = ArticleRef.parse(args.article)
    except ArticleRefError as e:
        return _err(str(e))
    loc = doc.locate(ref)
    badge = Basis(kind="law", law_name=doc.name, article=ref.display(), effective_date=doc.effective_date).badge()
    if args.json:
        print(json.dumps({"law_name": doc.name, "law_id": doc.law_id, "effective_date": str(doc.effective_date),
                          "article": ref.display(), "text": loc.text, "error": loc.error, "badge": badge,
                          "upcoming": [{"effective_date": str(v.effective_date), "change": v.change} for v in ups]},
                         ensure_ascii=False, indent=2))
        return 0 if loc.ok else 1
    print(head)
    if ups:
        print("시행예정: " + ", ".join(f"{v.effective_date}({v.change or '개정'})" for v in ups))
    print(f"근거 배지: {badge}")
    print("-" * 40)
    if loc.error:
        print(f"[못 찾음] {loc.error}")
    print(loc.text)
    return 0 if loc.ok else 1


# ---------------------------------------------------------------- 등록

def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "bank", "문제은행(문항 추가·법령 검증·앱 팩)")

    p = g.add_parser("add", help="새 문항 추가(draft)")
    p.add_argument("--file", required=True, help="문항 JSON 배열 파일")
    p.add_argument("--dry-run", action="store_true", help="검사만 하고 저장하지 않음")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_add)

    p = g.add_parser("update", help="기존 문항 고치기(version+1, draft)")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_update)

    p = g.add_parser("verify", help="법령 원문 대조 검증")
    p.add_argument("--status", default="draft", choices=["draft", "verified", "rejected"])
    p.add_argument("--id", action="append", help="문항 id(여러 번 또는 쉼표)")
    p.add_argument("--subject", action="append", help="과목 코드(RE CIV BRK PUB REG TAX)")
    p.add_argument("--offline", action="store_true", help="캐시만 사용")
    p.add_argument("--refresh", action="store_true", help="캐시를 무시하고 다시 받기")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = g.add_parser("crosscheck", help="독립 풀이 답 기록(다르면 rejected)")
    p.add_argument("--id", required=True)
    p.add_argument("--answer", required=True, help="ox: O/X · mcq: ①~⑤ · blank: 낱말 · order: 2,0,1,3 · calc: 숫자")
    p.add_argument("--by", default="claude")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_crosscheck)

    p = g.add_parser("stats", help="문항 통계")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = g.add_parser("list", help="문항 목록")
    p.add_argument("--status", action="append")
    p.add_argument("--subject", action="append")
    p.add_argument("--type", action="append")
    p.add_argument("--unused", action="store_true", help="콘텐츠에 아직 안 쓴 문항만")
    p.add_argument("--ready", action="store_true", help="콘텐츠·앱에 쓸 수 있는 문항만(verified + 독립 정답 확인 통과)")
    p.add_argument("--unit", help="단원 이름 일부")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = g.add_parser("show", help="문항 보기")
    p.add_argument("--id", required=True)
    p.add_argument("--blind", action="store_true", help="정답·해설 없이(독립 풀이용)")
    p.set_defaults(func=cmd_show)

    p = g.add_parser("export", help="앱용 팩 만들기")
    p.add_argument("--out", default="out/bank")
    p.add_argument("--allow-unchecked", action="store_true", help="독립 정답 확인 없이도 넣기(리허설용)")
    p.set_defaults(func=cmd_export)

    p = g.add_parser("upload", help="팩을 Supabase Storage 에 올리기")
    p.add_argument("--dir", default="out/bank")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_upload)

    p = g.add_parser("watch-laws", help="인용 법령 개정 감지 → retired")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="상태를 바꾸지 않고 보고만")
    p.add_argument("--date", help="기록 파일 날짜(기본 오늘, YYYY-MM-DD)")
    p.set_defaults(func=cmd_watch)

    p = g.add_parser("mark-used", help="콘텐츠에 쓴 문항 기록")
    p.add_argument("--asset", required=True, help="콘텐츠 ID(예: 2026W42-S03)")
    p.add_argument("--items", nargs="+", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_mark_used)

    p = g.add_parser("law", help="법령 조문 원문 보기(문항 쓰기 전에)")
    p.add_argument("--name", required=True, help="법령명(예: 공인중개사법)")
    p.add_argument("--article", help="조문(예: 제33조, 제33조 제1항 제6호). 없으면 조문 목록")
    p.add_argument("--decree", action="store_true", help="같은 법령의 시행령")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_law)
