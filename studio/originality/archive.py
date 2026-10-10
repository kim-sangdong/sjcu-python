"""과거 발행 지문 아카이브(archive/index.json).

구조: {"version": 1, "params": {...}, "assets": [{...}, ...]}
  studio.planning.build_brief 가 assets[i]["asset_id"], ["title"] 을 읽으므로 이 키는 바꾸지 않는다.
원문은 두지 않고 지문만 둔다(MinHash 정수 128개, 문장 해시 8자, pHash hex). 파일은 자산 한 줄씩(git diff 가 읽기 쉽게)
쓰며 1년 800개 자산 기준 약 3~4MB 다.
"""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from studio import paths
from studio.originality import text as T
from studio.originality.assets import Doc
from studio.originality.features import MAX_PHRASES, MIN_SENT, TextFx, VisFx
from studio.originality.thresholds import Thresholds

VERSION = 1
PARAMS = {
    "minhash": {"num_perm": T.NUM_PERM, "ngram": T.SHINGLE_N, "seed": T.MINHASH_SEED, "hash": "blake2b-32",
                "perm": "((a*h+b) mod 2^61-1) & 0xffffffff", "text": "본문(틀 장면·고정 문구 제외) 정규화"},
    "minhash_num": "minhash 와 같고 숫자 덩어리를 0 으로",
    "desc_minhash": {"num_perm": T.DESC_NUM_PERM, "ngram": T.SHINGLE_N},
    "phrase_hashes": "고정 문구 후보(틀 장면 문장 + 첫·끝 문장) sha1(정규화·숫자→0 문장)[:8]",
    "sentence_hashes": "모든 문장(정규화 6자 이상) 같은 해시 — 문장 재사용 비교",
    "phash_bits": 256,
}


class ArchiveError(RuntimeError):
    pass


def empty_index() -> dict:
    return {"version": VERSION, "params": PARAMS, "assets": []}


def load_index() -> dict:
    p = paths.archive_index()
    if not p.exists():
        return empty_index()
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except ValueError as e:
        raise ArchiveError(f"{p} 를 읽지 못했습니다({e}). git 에서 이전 판으로 되돌린 뒤 다시 실행하세요.") from e
    if not data:
        return empty_index()
    if data.get("version") != VERSION:
        raise ArchiveError(f"아카이브 version {data.get('version')} 은 이 검사기(version {VERSION})와 맞지 않습니다. "
                           "지문 규칙이 바뀌었으면 `python -m studio archive add` 로 과거 주차를 다시 등록하세요.")
    data.setdefault("assets", [])
    return data


def dump(index: dict) -> str:
    """자산 한 줄씩, 공백 없이."""
    assets = sorted(index.get("assets", []), key=lambda a: (a.get("week", ""), a.get("asset_id", "")))
    head = {k: v for k, v in index.items() if k != "assets"}
    head["version"] = VERSION
    head.setdefault("params", PARAMS)
    lines = ["{" + ",".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in head.items()) + ',"assets":[']
    for i, a in enumerate(assets):
        lines.append(json.dumps(a, ensure_ascii=False, separators=(",", ":")) + ("," if i < len(assets) - 1 else ""))
    lines.append("]}")
    return "\n".join(lines) + "\n"


@contextmanager
def editing() -> Iterator[dict]:
    """with editing() as idx: ...  — 잠근 상태에서 읽고, 블록이 끝나면 원자적으로 저장한다."""
    p = paths.archive_index()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            idx = load_index()
            yield idx
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(dump(idx), encoding="utf-8")
            tmp.replace(p)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def upsert(index: dict, entry: dict) -> None:
    index["assets"] = [a for a in index.get("assets", []) if a.get("asset_id") != entry["asset_id"]] + [entry]


def entry_from(doc: Doc, tx: TextFx, vis: VisFx | None, th: Thresholds) -> dict:
    """아카이브 지문 한 개."""
    sentences = list(dict.fromkeys(h for h, n in zip(tx.hashes, tx.norm) if len(n) >= MIN_SENT))[:MAX_PHRASES]
    phrases = [h for h in dict.fromkeys(tx.hashes) if h in tx.candidates]
    frames: list[str] = []
    if vis and vis.frames and vis.frames.hexes:
        hexes = vis.frames.hexes
        cap = int(th.opt("archive_max_frames"))
        if len(hexes) > cap:
            step = len(hexes) / cap
            hexes = [hexes[int(i * step)] for i in range(cap)]
        frames = hexes
    e = {
        "asset_id": doc.asset_id,
        "kind": doc.kind,
        "week": doc.week,
        "title": doc.title,
        "title_head": tx.head,
        "format": doc.format if doc.kind in ("short", "longform") else None,
        "skeleton": doc.skeleton,
        "hashtags": sorted(tx.hashtags),
        "tags": sorted(tx.tags),
        "minhash": tx.mh,
        "minhash_num": tx.mh_num,
        "desc_minhash": tx.desc_mh if tx.desc_len >= 20 else None,
        "phrase_hashes": phrases,
        "sentence_hashes": sentences,
        "thumb_phash": vis.thumb.hex if vis and vis.thumb else None,
        "thumb_edge_phash": vis.thumb.edge_hex if vis and vis.thumb else None,
        "frame_phashes": frames,
        "voice_hashes": tx.voice_hashes,
        "added_at": datetime.now(paths.KST).isoformat(timespec="seconds"),
    }
    if doc.sources:
        e["sources"] = sorted(doc.sources)
    return e
