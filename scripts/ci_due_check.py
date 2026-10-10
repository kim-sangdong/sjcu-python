#!/usr/bin/env python3
"""publish-due 워크플로 앞단 점검 — 이번 실행에 처리할 발행 대기열이 있는지 본다(표준 라이브러리만, 설치 전에 돈다).

    python3 scripts/ci_due_check.py [--root .] [--now 2026-10-12T12:07:00+09:00] [--force]

출력(GITHUB_OUTPUT 이 있으면 거기에, 아니면 표준출력):
    run=true|false        publish due 를 돌릴지
    tistory=true|false    티스토리용 Playwright 크로미움이 필요한지
    reason=<한 줄 설명>

판단은 studio.publish.queue.action_for 보다 넓게 잡는다(놓치는 것보다 한 번 더 도는 편이 낫다).
  - uploading · processing 상태가 있으면 처리 대상(멈춘 업로드 정리·처리 상태 확인)
  - pending 이고 publish_at <= 지금 + LOOKAHEAD 이면 처리 대상
  - 대기열을 읽지 못하면 run=true(실제 명령이 오류를 보여 주게)
--force(수동 실행) 이면 항상 run=true, 대기 중인 티스토리 항목이 하나라도 있으면 tistory=true.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
RECENT_WEEKS = 3                      # studio.publish.queue.RECENT_WEEKS 와 같게
LOOKAHEAD = timedelta(minutes=20)     # publish due 기본 앞당김(10분)보다 넉넉하게
ACTIVE = ("pending", "uploading", "processing")


def week_of(d) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def publishing_week(now: datetime) -> str:
    """studio.paths.publishing_week 와 같은 규칙: 지금(KST) 다음 날이 속한 ISO 주."""
    return week_of((now.astimezone(KST) + timedelta(days=1)).date())


def recent_weeks(now: datetime, n: int = RECENT_WEEKS) -> list[str]:
    weeks = [publishing_week(now)]
    for _ in range(n - 1):
        y, w = int(weeks[0][:4]), int(weeks[0][6:])
        monday = datetime.fromisocalendar(y, w, 1).date()
        weeks.insert(0, week_of(monday - timedelta(days=7)))
    return weeks


def parse_time(s: str) -> datetime:
    t = datetime.fromisoformat(s)   # 3.11+ 는 끝의 Z 도 읽는다
    return t if t.tzinfo else t.replace(tzinfo=KST)


def scan(root: Path, now: datetime, *, force: bool = False) -> dict[str, str]:
    run = tistory = False
    reasons: list[str] = []
    for week in recent_weeks(now):
        q = root / "content" / "weeks" / week / "queue.json"
        if not q.exists():
            continue
        try:
            targets = json.loads(q.read_text(encoding="utf-8")).get("targets") or []
        except (OSError, ValueError, AttributeError) as e:
            return {"run": "true", "tistory": "true", "reason": f"{week} 대기열을 읽지 못함({type(e).__name__}) — 실제 명령으로 확인"}
        for t in targets:
            status = str(t.get("status", ""))
            if status not in ACTIVE:
                continue
            try:
                due = status != "pending" or parse_time(str(t.get("publish_at"))) <= now + LOOKAHEAD
            except (TypeError, ValueError):
                due = True
            is_tistory = t.get("platform") == "tistory"
            if due:
                run = True
                tistory = tistory or is_tistory
                reasons.append(f"{week} {t.get('platform')}:{t.get('asset_id')} {status}")
            elif force and is_tistory:
                tistory = True
    if force:
        run = True
        reasons.insert(0, "수동 실행(--force)")
    reason = "; ".join(reasons[:8]) + (f" 외 {len(reasons) - 8}건" if len(reasons) > 8 else "") if reasons else "처리할 항목 없음"
    return {"run": str(run).lower(), "tistory": str(tistory).lower(), "reason": reason}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--now", help="기준 시각(ISO). 시간대가 없으면 KST")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    now = parse_time(args.now) if args.now else datetime.now(UTC)
    res = scan(Path(args.root), now, force=args.force)
    lines = [f"{k}={v}" for k, v in res.items()]
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
