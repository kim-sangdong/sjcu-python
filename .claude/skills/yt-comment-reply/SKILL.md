---
description: 유튜브 새 댓글을 모아 답글을 쓰고 올린다. 매일 21:00 KST 에 GitHub Actions 가 부른다.
argument-hint: "[--since-hours 48] [--dry-run]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Read Write Edit Glob Grep
---

# /yt-comment-reply

인자: `$ARGUMENTS` (`--dry-run` 이면 post 단계에 `--dry-run` 을 붙인다)

1. `python -m studio comments fetch --since-hours 48` (인자에 다른 값이 있으면 그 값). 출력된 `content/comments/<날짜>/inbox.json` 을 읽는다.
2. `docs/CONTENT_GUIDE.md` 의 "댓글 응대 규칙"을 읽고 댓글마다 판단한다.
   - 답하지 않는다(`skip: true`, `reason` 기록): 스팸·광고, 욕설·비방, 개인정보가 담긴 글, 정치·종교 논쟁, flags 에 spam 이 있는 글.
   - 질문: 영상·원고에서 확인되는 범위로만 짧게 답한다. 확인할 수 없는 내용은 추측하지 않고 "확인해서 다음 영상에서 다루겠다"고 답한다. 개인 상황에 대한 전문 조언(의료·법률·투자 등)은 하지 않는다.
   - 오류 지적: 맞는 지적이면 고맙다고 하고 정정 내용을 쓴다. 정정 목록을 `content/comments/<날짜>/corrections.md` 에 남긴다(다음 주 기획 입력).
   - 칭찬·감상: 한두 문장으로 고맙다는 인사 + 영상 내용과 연결된 한마디. 같은 문장을 반복하지 않는다.
   - 답글은 존댓말, 200자 이내, 링크는 넣지 않는다.
3. `content/comments/<날짜>/replies.json` 에 `[{"comment_id": "...", "reply": "...", "skip": false, "reason": ""}]` 형식으로 저장한다.
4. `python -m studio comments post --file content/comments/<날짜>/replies.json` 로 올린다. 거부된 항목이 있으면 이유를 읽고, 고칠 수 있으면 고쳐 한 번만 다시 올린다.
5. 결과를 3줄로 요약해 출력한다(답글 수, 건너뜀 수, 정정 필요 건).
