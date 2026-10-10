"""댓글 명령.

  python -m studio comments fetch [--since-hours 48] [--max 200] [--dry-run]
  python -m studio comments post --file content/comments/<날짜>/replies.json [--max 30] [--dry-run]

종료 코드: 0 정상, 1 거부·실패 항목 있음 또는 API 오류, 2 입력 파일 문제, 3 인증·설정 필요(진입점이 처리).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from studio import paths
from studio.cliutil import group
from studio.config import load_config


def _is_dry(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False)) or os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def _transient() -> tuple[type[BaseException], ...]:
    """토큰 서버 일시 오류(google.auth RefreshError·TransportError)와 네트워크 오류."""
    from google.auth.exceptions import GoogleAuthError as LibAuthError

    from studio.publish.youtube import transient_errors

    return (LibAuthError, *transient_errors())


def _api_failure(e: Exception, action: str) -> int:
    from studio.publish.youtube import to_publish_error

    print(f"[오류] {to_publish_error(e, action)}", file=sys.stderr)
    return 1


def cmd_fetch(args: argparse.Namespace) -> int:
    from googleapiclient.errors import HttpError

    from studio.comments import store
    from studio.comments.fetch import PAGE_SIZE, THREAD_PARTS, fetch_inbox

    cfg = load_config()
    if _is_dry(args):
        print("[dry-run] 네트워크를 쓰지 않습니다. 보낼 요청:")
        print(f"  commentThreads.list part={THREAD_PARTS} allThreadsRelatedToChannelId=<YOUTUBE_CHANNEL_ID 또는 channels.list mine=true> "
              f"order=time moderationStatus=published textFormat=plainText maxResults={PAGE_SIZE} (최근 {args.since_hours:g}시간, 최대 {args.max}건)")
        print("  commentThreads.list moderationStatus=heldForReview (건수 요약) / videos.list part=snippet (영상 제목)")
        print(f"  결과 위치: {_rel(store.day_dir() / 'inbox.json')}")
        return 0
    try:
        rep = fetch_inbox(cfg, since_hours=args.since_hours, max_items=args.max)
    except HttpError as e:
        return _api_failure(e, "commentThreads.list")
    except _transient() as e:
        print(f"[오류] YouTube·Google 서버 연결 실패({type(e).__name__}: {str(e)[:200]}) — 잠시 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    inbox = rep.out_dir / "inbox.json"
    spam = sum(1 for i in rep.items if "spam" in i["flags"])
    print(f"새 댓글 {len(rep.items)}건 → {_rel(inbox)} (스팸 의심 {spam}건, 훑어본 스레드 {rep.scanned}개)")
    if rep.excluded:
        print("제외: " + ", ".join(f"{k} {v}" for k, v in rep.excluded.items()))
    if rep.held_for_review is None:
        print("검토 대기(heldForReview): 확인 실패")
    elif rep.held_for_review:
        print(f"검토 대기(heldForReview) {rep.held_for_review}건 — YouTube Studio → 댓글 → '검토 대기' 에서 사람이 승인·삭제하세요.")
    else:
        print("검토 대기(heldForReview) 0건")
    print(_rel(inbox))
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    from studio.comments.post import RepliesFileError, post_replies

    cfg = load_config()
    path = Path(args.file)
    if not path.is_absolute():
        path = (Path.cwd() / path) if (Path.cwd() / path).exists() else paths.ROOT / path
    dry = _is_dry(args)
    try:
        rep = post_replies(path, cfg, dry_run=dry, max_posts=args.max)
    except RepliesFileError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2
    except _transient() as e:
        print(f"[오류] YouTube·Google 서버 연결 실패({type(e).__name__}: {str(e)[:200]}) — 잠시 뒤 다시 실행하세요. "
              "이미 올린 답글은 장부에 남아 다시 올리지 않습니다.", file=sys.stderr)
        return 1
    label = {"posted": "올림", "skipped": "건너뜀", "already": "이미 답함", "duplicate": "중복",
             "rejected": "거부", "deferred": "보류", "failed": "실패"}
    for r in rep.results:
        if r["status"] in ("rejected", "failed", "deferred", "duplicate") or r.get("warnings"):
            reasons = "; ".join(r.get("reasons") or []) or "; ".join(r.get("warnings") or [])
            print(f"[{label[r['status']]}] #{r['index']} {r['comment_id']}: {reasons}")
        elif dry and r["status"] == "posted":
            print(f"[dry-run] comments.insert parentId={r['comment_id']} textOriginal={r['reply'][:60]!r}")
    summary = ", ".join(f"{label[s]} {rep.count(s)}" for s in label if rep.count(s))
    print(("[dry-run] " if dry else "") + f"답글 결과: {summary or '처리할 항목 없음'}")
    if rep.stopped:
        print(f"[중단] {rep.stopped}", file=sys.stderr)
    if rep.count("rejected"):
        print("거부된 답글은 이유대로 고친 뒤 같은 명령으로 다시 올리세요(이미 올린 답글은 장부로 건너뜁니다).")
    return 0 if rep.ok else 1


def register(sub: argparse._SubParsersAction) -> None:
    g = group(sub, "comments", "유튜브 댓글 수집·답글")
    f = g.add_parser("fetch", help="새 댓글을 모아 content/comments/<날짜>/inbox.json 에 쓰기")
    f.add_argument("--since-hours", type=float, default=48, help="최근 몇 시간 안의 댓글 (기본 48)")
    f.add_argument("--max", type=int, default=200, help="inbox 최대 건수 (기본 200)")
    f.add_argument("--dry-run", action="store_true", help="API 를 부르지 않고 보낼 요청만 출력")
    f.set_defaults(func=cmd_fetch)
    p = g.add_parser("post", help="replies.json 의 답글을 안전장치 검사 후 올리기")
    p.add_argument("--file", required=True, help="content/comments/<날짜>/replies.json")
    p.add_argument("--max", type=int, default=30, help="이번 실행에서 올릴 최대 답글 수 (기본 30)")
    p.add_argument("--dry-run", action="store_true", help="올리지 않고 검사 결과와 보낼 요청만 출력(장부 기록 안 함)")
    p.set_defaults(func=cmd_post)
