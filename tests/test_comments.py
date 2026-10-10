"""댓글 수집·답글 테스트. googleapiclient 서비스는 HttpMockSequence 로 만든다(네트워크 없음)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from googleapiclient.discovery import build
from googleapiclient.http import HttpMockSequence

from studio import config, paths
from studio.comments import cli as comments_cli
from studio.comments import filters, post, store
from studio.comments.fetch import fetch_inbox
from studio.config import Config
from studio.publish import google_auth

CH = "UCchannel0000000000000"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
LINK_BASE = "https://ref.supabase.co/functions/v1/go"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    monkeypatch.setenv("YOUTUBE_CHANNEL_ID", CH)
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(post, "_sleep", lambda s: None)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


def _cfg(link_base: str = LINK_BASE) -> Config:
    return Config.model_validate({
        "app": {"name": "복습노트", "link_base": link_base},
        "rules": {"banned_phrases": ["치매 예방", "합격 보장"]},
    })


def _svc(responses: list) -> tuple:
    http = HttpMockSequence(responses)
    return build("youtube", "v3", http=http, static_discovery=True), http


def _ok(data: dict) -> tuple:
    return ({"status": "200"}, json.dumps(data))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _thread(tid: str, text: str, *, hours_ago: float = 2, author: str = "UCviewer", video: str = "VID1",
            replies: list | None = None, total: int | None = None, can_reply: bool = True) -> dict:
    reps = replies or []
    return {
        "id": tid,
        "snippet": {
            "channelId": CH, "videoId": video, "canReply": can_reply,
            "totalReplyCount": len(reps) if total is None else total,
            "topLevelComment": {"id": tid, "snippet": {
                "authorDisplayName": f"시청자{tid}", "authorChannelId": {"value": author},
                "textDisplay": text, "videoId": video, "likeCount": 3,
                "publishedAt": _iso(NOW - timedelta(hours=hours_ago)),
            }},
        },
        "replies": {"comments": reps},
    }


def _reply_by(author: str) -> dict:
    return {"id": "r1", "snippet": {"authorChannelId": {"value": author}, "textDisplay": "답글"}}


def _q(uri: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(uri).query)


# ---------------------------------------------------------------- filters

def test_find_hosts():
    assert filters.find_hosts("여기 https://bit.ly/abc 보세요") == ["bit.ly"]
    assert filters.find_hosts("www.example.co.kr/path 참고") == ["www.example.co.kr"]
    assert filters.find_hosts("주소는example.com입니다") == ["example.com"]
    assert filters.find_hosts("메일 me@test.com 으로") == []
    assert filters.find_hosts("3.5점, 되/돼, e.g. 예시, 1.2.3 버전") == []


def test_comment_flags():
    cfg = _cfg()
    assert filters.comment_flags("되/돼 구분은 어떻게 하나요?", cfg) == ["question"]
    laugh = filters.comment_flags("ㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋㅋ 재밌어요", cfg)
    assert "repeat" in laugh and "spam" not in laugh
    assert filters.comment_flags("구독 구독 구독 구독 해주세요", cfg)[0] == "spam"
    ad = filters.comment_flags("고수익 부업 텔레그램 문의 t.me/abc", cfg)
    assert ad[0] == "spam" and "link" in ad and "spam_phrase" in ad
    assert "banned_phrase" in filters.comment_flags("이거 치매예방에 좋나요", cfg)
    assert "personal_info" in filters.comment_flags("제 번호 010-1234-5678 로 연락", cfg)


def test_reply_problems():
    cfg = _cfg()
    assert filters.reply_problems("고맙습니다. 다음 영상에서 더 다룰게요.", cfg) == []
    assert any("금지 표현" in p for p in filters.reply_problems("치매 예방 효과가 있어요", cfg))
    assert any("외부 링크 'example.com'" in p for p in filters.reply_problems("example.com 참고하세요", cfg))
    assert filters.reply_problems(f"앱은 여기서 받으세요 {LINK_BASE}?s=youtube", cfg) == []
    assert any("외부 링크" in p for p in filters.reply_problems(f"{LINK_BASE}", _cfg(link_base="")))
    assert any("501자" in p for p in filters.reply_problems("가" * 501, cfg))
    assert filters.reply_problems("   ", cfg) == ["답글이 비어 있습니다"]


# ---------------------------------------------------------------- fetch

def test_fetch_builds_inbox(root):
    store.record("T3", {"status": "skipped", "reason": "스팸"})        # 이미 처리한 스레드
    q = root / "content" / "weeks" / "2026-W42" / "queue.json"
    q.parent.mkdir(parents=True)
    q.write_text(json.dumps({"week": "2026-W42", "targets": [
        {"asset_id": "2026W42-L1", "platform": "youtube", "remote_id": "VID1"}]}), encoding="utf-8")

    page1 = {"nextPageToken": "P2", "items": [
        _thread("T1", "되/돼 구분은 어떻게 하나요?"),
        _thread("T2", "잘 봤어요", replies=[_reply_by(CH)]),
        _thread("T3", "처리했던 댓글"),
        _thread("T4", "고수익 부업 텔레그램 https://bit.ly/xx", video="VID2"),
        _thread("T5", "채널 공지 댓글", author=CH),
        _thread("T6", "답글 많은 댓글", replies=[_reply_by("UCother")], total=7),
        _thread("T7", "오래된 댓글", hours_ago=24 * 5),
        _thread("T8", "답글 막힌 댓글", can_reply=False),
    ]}
    page2 = {"items": [_thread("T9", "더 오래된 댓글", hours_ago=24 * 9)], "nextPageToken": "P3"}
    yt, http = _svc([
        _ok(page1),
        _ok(page2),
        _ok({"items": [_reply_by("UCother"), _reply_by(CH)]}),                         # comments.list(T6)
        _ok({"items": [{"id": "VID1", "snippet": {"title": "맞춤법 정리"}}, {"id": "VID2", "snippet": {"title": "쇼츠"}}]}),
        _ok({"items": [{"id": "H1"}, {"id": "H2"}]}),                                  # heldForReview
    ])
    rep = fetch_inbox(_cfg(), since_hours=48, max_items=200, yt=yt, now=NOW)

    inbox_path = root / "content" / "comments" / "2026-10-10" / "inbox.json"
    inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
    assert [i["thread_id"] for i in inbox] == ["T1", "T4"]
    t1, t4 = inbox
    assert set(t1) >= {"thread_id", "comment_id", "video_id", "video_title", "author", "text", "published_at", "like_count", "flags"}
    assert t1["video_title"] == "맞춤법 정리" and t1["asset_id"] == "2026W42-L1" and t1["flags"] == ["question"]
    assert t4["flags"][0] == "spam" and "link" in t4["flags"] and t4["asset_id"] is None
    assert rep.held_for_review == 2
    summary = json.loads((inbox_path.parent / "summary.json").read_text(encoding="utf-8"))
    assert summary["held_for_review"] == 2 and summary["new"] == 2 and summary["spam_flagged"] == 1
    assert summary["excluded"] == {"채널이 이미 답함": 2, "이미 처리함(장부)": 1, "채널 본인 댓글": 1,
                                   "기간 밖": 2, "답글 불가": 1}

    seq = http.request_sequence
    first = _q(seq[0][0])
    assert first["allThreadsRelatedToChannelId"] == [CH] and first["order"] == ["time"]
    assert first["moderationStatus"] == ["published"] and first["part"] == ["snippet,replies"]
    assert _q(seq[1][0])["pageToken"] == ["P2"]
    assert len(seq) == 5                                     # 기간 밖만 나온 2쪽 뒤로는 더 넘기지 않음
    assert _q(seq[2][0])["parentId"] == ["T6"]
    assert _q(seq[4][0])["moderationStatus"] == ["heldForReview"]


def test_fetch_resolves_channel_when_env_missing(root, monkeypatch):
    monkeypatch.delenv("YOUTUBE_CHANNEL_ID")
    monkeypatch.setattr(config, "_env_loaded", True)
    yt, http = _svc([
        _ok({"items": [{"id": CH}]}),                            # channels.list mine=true
        _ok({"items": []}),
        _ok({"items": []}),
    ])
    rep = fetch_inbox(_cfg(), yt=yt, now=NOW)
    assert rep.channel_id == CH and rep.items == []
    assert _q(http.request_sequence[0][0])["mine"] == ["true"]


# ---------------------------------------------------------------- post

def _write_replies(root, items: list, inbox_ids: list[str] | None = None):
    d = root / "content" / "comments" / "2026-10-10"
    d.mkdir(parents=True, exist_ok=True)
    if inbox_ids is not None:
        (d / "inbox.json").write_text(json.dumps([{"comment_id": c, "thread_id": c, "video_id": "VID1"} for c in inbox_ids]), encoding="utf-8")
    p = d / "replies.json"
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return p


def test_post_safety_and_ledger(root, monkeypatch):
    store.record("C11", {"status": "replied", "reply_id": "old", "reply": "예전 답글"})
    items = [
        {"comment_id": "C1", "reply": "질문 고맙습니다. '돼'는 '되어'로 바꿔 보면 됩니다."},
        {"comment_id": "C2", "skip": True, "reason": "광고"},
        {"comment_id": "C3", "reply": "꾸준히 하면 치매 예방에 좋아요"},
        {"comment_id": "C4", "reply": "자세한 건 https://example.com 에서"},
        {"comment_id": "C5", "reply": f"앱은 여기서 받으세요: {LINK_BASE}?s=youtube&m=comment"},
        {"comment_id": "C6", "reply": "가" * 501},
        {"comment_id": "C7", "reply": "시청해 주셔서 감사합니다!"},
        {"comment_id": "C8", "reply": "시청해 주셔서 감사합니다"},
        {"comment_id": "C9", "reply": "시청해 주셔서, 감사합니다!!"},
        {"comment_id": "C1", "reply": "두 번째 답글"},
        {"comment_id": "C11", "reply": "다시 답하기"},
        {"reply": "ID 없음"},
    ]
    path = _write_replies(root, items, inbox_ids=[f"C{i}" for i in range(1, 12)])
    yt, http = _svc([_ok({"id": f"R{i}"}) for i in (1, 5, 7, 8)])
    rep = post.post_replies(path, _cfg(), yt=yt, now=NOW)

    status = {r["index"]: r["status"] for r in rep.results}
    assert status == {1: "posted", 2: "skipped", 3: "rejected", 4: "rejected", 5: "posted", 6: "rejected",
                      7: "posted", 8: "posted", 9: "rejected", 10: "duplicate", 11: "already", 12: "rejected"}
    assert not rep.ok
    reasons = {r["index"]: " ".join(r.get("reasons") or []) for r in rep.results}
    assert "금지 표현 '치매 예방'" in reasons[3]
    assert "example.com" in reasons[4] and "500자" in reasons[6] and "같은 문구" in reasons[9]

    sent = [json.loads(b) for _, _, b, _ in http.request_sequence]
    assert [s["snippet"]["parentId"] for s in sent] == ["C1", "C5", "C7", "C8"]
    assert sent[0]["snippet"]["textOriginal"].startswith("질문 고맙습니다")
    assert "part=snippet" in http.request_sequence[0][0]

    led = store.load_ledger()["comments"]
    assert led["C1"]["status"] == "replied" and led["C1"]["reply_id"] == "R1" and led["C1"]["thread_id"] == "C1"
    assert led["C2"]["status"] == "skipped" and led["C2"]["reason"] == "광고"
    assert "C3" not in led and "C9" not in led                     # 거부된 항목은 고쳐서 다시 올릴 수 있게 기록하지 않음
    result = json.loads(path.with_name("replies.result.json").read_text(encoding="utf-8"))
    assert result["summary"]["posted"] == 4 and result["summary"]["rejected"] == 5

    # 같은 파일을 다시 올려도 중복 답글이 생기지 않는다(API 호출 없음)
    def never(scopes=None):
        raise AssertionError("이미 답한 댓글에 다시 요청하면 안 됩니다")

    monkeypatch.setattr(google_auth, "youtube_service", never)
    path2 = _write_replies(root, items[:2] + items[4:5] + items[6:8])
    rep2 = post.post_replies(path2, _cfg(), now=NOW)
    assert {r["status"] for r in rep2.results} == {"already", "skipped"}


def test_post_max_per_run_and_dry_run(root, monkeypatch):
    def never(scopes=None):
        raise AssertionError("dry-run 은 API 를 부르지 않습니다")

    monkeypatch.setattr(google_auth, "youtube_service", never)
    items = [{"comment_id": f"C{i}", "reply": f"{i}번째 댓글 고맙습니다. 내용 {i}"} for i in range(1, 4)]
    path = _write_replies(root, items)
    rep = post.post_replies(path, _cfg(), dry_run=True, max_posts=2, now=NOW)
    assert [r["status"] for r in rep.results] == ["posted", "posted", "deferred"]
    assert rep.results[0]["reply_id"].startswith("dryrun-")
    assert not store.ledger_path().exists()                    # dry-run 은 장부를 건드리지 않음
    assert path.with_name("replies.dryrun.result.json").exists()


def test_post_quota_stops_and_defers(root):
    items = [{"comment_id": f"C{i}", "reply": f"고맙습니다 {i}번"} for i in range(1, 4)]
    path = _write_replies(root, items)
    quota = ({"status": "403"}, json.dumps({"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}))
    yt, http = _svc([_ok({"id": "R1"}), quota])
    rep = post.post_replies(path, _cfg(), yt=yt, now=NOW)
    assert [r["status"] for r in rep.results] == ["posted", "deferred", "deferred"]
    assert "할당량" in rep.stopped and len(http.request_sequence) == 2
    assert set(store.load_ledger()["comments"]) == {"C1"}


def test_post_deleted_comment_fails_item_only(root):
    items = [{"comment_id": "C1", "reply": "첫 답글입니다"}, {"comment_id": "C2", "reply": "둘째 답글입니다"}]
    path = _write_replies(root, items)
    gone = ({"status": "404"}, json.dumps({"error": {"code": 404, "message": "not found", "errors": [{"reason": "commentNotFound"}]}}))
    yt, _ = _svc([gone, _ok({"id": "R2"})])
    rep = post.post_replies(path, _cfg(), yt=yt, now=NOW)
    assert [r["status"] for r in rep.results] == ["failed", "posted"]
    assert "삭제" in rep.results[0]["reasons"][0]


def test_same_reply_counts_recent_history(root):
    for i in range(2):
        store.record(f"OLD{i}", {"status": "replied", "reply": "좋은 하루 보내세요!"})
    path = _write_replies(root, [{"comment_id": "C1", "reply": "좋은 하루 보내세요"}])
    rep = post.post_replies(path, _cfg(), dry_run=True, now=datetime.now(timezone.utc))
    assert rep.results[0]["status"] == "rejected"


# ---------------------------------------------------------------- CLI

def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    comments_cli.register(sub)
    args = parser.parse_args(["comments", *argv])
    return args.func(args)


def test_cli_post_exit_codes(root, monkeypatch, capsys):
    monkeypatch.setattr(comments_cli, "load_config", lambda path=None: _cfg())
    path = _write_replies(root, [{"comment_id": "C1", "reply": "치매 예방 됩니다"}])
    assert _cli(["post", "--file", str(path), "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "[거부]" in out and "금지 표현" in out

    path.write_text(json.dumps({"not": "list"}), encoding="utf-8")
    assert _cli(["post", "--file", str(path)]) == 2

    path = _write_replies(root, [{"comment_id": "C1", "reply": "고맙습니다"}])
    assert _cli(["post", "--file", str(path), "--dry-run"]) == 0
    assert "comments.insert parentId=C1" in capsys.readouterr().out


def test_cli_fetch_dry_run(root, monkeypatch, capsys):
    monkeypatch.setattr(comments_cli, "load_config", lambda path=None: _cfg())
    monkeypatch.setattr(google_auth, "youtube_service", lambda scopes=None: (_ for _ in ()).throw(AssertionError("API 금지")))
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    assert _cli(["fetch", "--since-hours", "24"]) == 0
    out = capsys.readouterr().out
    assert "allThreadsRelatedToChannelId" in out and "heldForReview" in out
