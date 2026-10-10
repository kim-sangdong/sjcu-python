"""문제은행 파일 읽기·쓰기 — content/bank/<과목코드>.jsonl.

- 한 줄 한 문항, id 순 정렬. 바뀐 과목 파일만 다시 쓴다(원자적 교체).
- 여러 프로세스(주간 파이프라인·스킬·사람)가 동시에 고쳐도 깨지지 않게 out/locks/bank.lock 으로 잠근다.
- 깨진 줄이 있으면 파일:줄 번호와 함께 BankError 를 낸다(조용히 버리지 않는다).
"""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from pydantic import ValidationError

from studio import paths
from studio.bank.models import now as bank_now
from studio.bank.models import SUBJECTS, BankItem, VerifyEntry, item_line

LOG_KEEP = 60   # 문항별 verify_log 보존 개수(최근 것 + 필요한 기록은 따로 지킨다)


class BankError(RuntimeError):
    pass


def bank_dir() -> Path:
    return paths.CONTENT / "bank"


def subject_file(code: str) -> Path:
    return bank_dir() / f"{code}.jsonl"


def law_changes_dir() -> Path:
    return bank_dir() / "law_changes"


def lock_path() -> Path:
    return paths.OUT / "locks" / "bank.lock"


def _read_file(p: Path) -> list[BankItem]:
    items: list[BankItem] = []
    if not p.exists():
        return items
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            items.append(BankItem.model_validate_json(line))
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(x) for x in first.get("loc", ()))
            raise BankError(f"{p.relative_to(paths.ROOT) if p.is_relative_to(paths.ROOT) else p}:{n} 문항 형식 오류 — {loc}: {first.get('msg')}") from e
        except json.JSONDecodeError as e:   # pragma: no cover - pydantic 이 먼저 잡는다
            raise BankError(f"{p}:{n} JSON 오류 — {e}") from e
    return items


class Bank:
    """메모리에 올린 문제은행. editing() 블록 안에서 고치면 블록 끝에 저장된다."""

    def __init__(self, items: Iterable[BankItem] = ()):
        self.items: dict[str, BankItem] = {}
        for it in items:
            if it.id in self.items:
                raise BankError(f"같은 id 가 두 번 있습니다: {it.id}")
            self.items[it.id] = it
        self._snapshot = self._serialize()

    # ------------------------------------------------------------ 조회

    def get(self, item_id: str) -> BankItem | None:
        return self.items.get(item_id)

    def require(self, item_id: str) -> BankItem:
        it = self.items.get(item_id)
        if it is None:
            raise BankError(f"문항을 찾지 못했습니다: {item_id}")
        return it

    def all(self) -> list[BankItem]:
        return [self.items[k] for k in sorted(self.items)]

    def select(self, *, ids: Iterable[str] | None = None, statuses: Iterable[str] | None = None,
               subjects: Iterable[str] | None = None, types: Iterable[str] | None = None) -> list[BankItem]:
        ids = list(ids or [])
        if ids:
            return [self.require(i) for i in ids]
        st, sj, tp = set(statuses or []), set(subjects or []), set(types or [])
        return [it for it in self.all()
                if (not st or it.status in st) and (not sj or it.subject in sj) and (not tp or it.type in tp)]

    # ------------------------------------------------------------ 변경

    def put(self, item: BankItem) -> None:
        self.items[item.id] = item

    # ------------------------------------------------------------ 저장

    def _serialize(self) -> dict[str, str]:
        out: dict[str, list[str]] = {c: [] for c in SUBJECTS}
        for it in self.all():
            trim_log(it)
            out[it.subject].append(item_line(it))
        return {c: ("\n".join(lines) + "\n") if lines else "" for c, lines in out.items()}

    def save(self) -> list[Path]:
        data = self._serialize()
        written: list[Path] = []
        for code, text in data.items():
            if text == self._snapshot.get(code, ""):
                continue
            p = subject_file(code)
            p.parent.mkdir(parents=True, exist_ok=True)
            if not text and not p.exists():
                continue
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(p)
            written.append(p)
        self._snapshot = data
        return written


def load_bank(codes: Iterable[str] | None = None) -> Bank:
    items: list[BankItem] = []
    for code in (codes or SUBJECTS):
        items.extend(_read_file(subject_file(code)))
    return Bank(items)


@contextmanager
def locked() -> Iterator[None]:
    lp = lock_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def editing() -> Iterator[Bank]:
    """with editing() as bank: ... — 잠근 상태에서 읽고, 블록이 예외 없이 끝나면 저장한다."""
    with locked():
        bank = load_bank()
        yield bank
        bank.save()


def trim_log(item: BankItem, keep: int = LOG_KEEP) -> None:
    """verify_log 를 최근 keep 개로 줄이되, 개정 감지에 쓰는 source_hash(근거별 최신)와
    현재 버전의 llm_crosscheck 기록은 남긴다."""
    log = item.verify_log
    if len(log) <= keep:
        return
    must: set[int] = set()
    latest_hash: dict[str, int] = {}
    for i, e in enumerate(log):
        if e.check == "source_hash":
            latest_hash[e.detail.split("|", 1)[0]] = i
        elif e.check == "llm_crosscheck" and e.detail.startswith(f"v{item.version} "):
            must.add(i)
    must.update(latest_hash.values())
    tail = set(range(len(log) - keep, len(log)))
    item.verify_log = [e for i, e in enumerate(log) if i in tail or i in must]


def last_entries(item: BankItem, check: str) -> list[VerifyEntry]:
    return [e for e in item.verify_log if e.check == check]


def touch(item: BankItem, when: datetime | None = None) -> None:
    item.updated_at = when or bank_now()
