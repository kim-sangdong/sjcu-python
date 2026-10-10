"""유튜브 댓글 수집·답글 (/yt-comment-reply 스킬이 사용).

  python -m studio comments fetch [--since-hours 48] [--max 200] [--dry-run]
  python -m studio comments post --file content/comments/<날짜>/replies.json [--max 30] [--dry-run]

파일
  content/comments/<YYYY-MM-DD>/inbox.json     답할 후보 댓글 (fetch 결과)
  content/comments/<YYYY-MM-DD>/summary.json   수집 요약(검토 대기 건수 포함)
  content/comments/<YYYY-MM-DD>/replies.json   Claude 가 쓴 답글 [{comment_id, reply, skip?, reason?}]
  content/comments/<YYYY-MM-DD>/replies.result.json  post 결과
  content/comments/replied.json                처리 장부(답함·건너뜀) — 중복 답글 방지
"""
