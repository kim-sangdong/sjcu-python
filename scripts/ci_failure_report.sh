#!/usr/bin/env bash
# 정기 실행 실패 알림: 작업 요약(GITHUB_STEP_SUMMARY)에 쓰고, 같은 제목의 열린 이슈가 없으면 만들고 있으면 본문을 갱신한다.
# (본문 갱신은 알림이 가지 않아 매시간 실패해도 메일이 쏟아지지 않는다. 처음 한 번만 알림이 간다.)
#
#   bash scripts/ci_failure_report.sh "<워크플로 이름>" [<요약 파일>]
#
# 필요: GH_TOKEN(=secrets.GITHUB_TOKEN), 워크플로 권한 issues: write.
# 알림이 실패해도 0 으로 끝낸다 — 원래 실패를 가리지 않게.
set -uo pipefail

name="${1:-${GITHUB_WORKFLOW:-workflow}}"
detail_file="${2:-}"
title="[자동 실행 실패] ${name}"
label="automation-failure"
server="${GITHUB_SERVER_URL:-https://github.com}"
run_url="${server}/${GITHUB_REPOSITORY:-local}/actions/runs/${GITHUB_RUN_ID:-0}"
now="$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M KST')"
tmp="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
sha="${GITHUB_SHA:-unknown}"
body="${tmp}/failure-issue-body.md"

# 이슈 본문은 로그와 달리 비밀값이 가려지지 않는다 → 요약 파일에서 환경변수의 비밀값을 *** 로 지운다.
if [ -n "$detail_file" ] && [ -s "$detail_file" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$detail_file" <<'PY' || : > "$detail_file"
import os, re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8", errors="replace").read()
secret_like = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|CREDENTIALS|_B64$|_AUTH$|^LAW_API_OC$|^SUPABASE_URL$|_ID$")
values = sorted({v.strip() for k, v in os.environ.items() if secret_like.search(k) and len(v.strip()) >= 6}, key=len, reverse=True)
for v in values:
    text = text.replace(v, "***")
open(path, "w", encoding="utf-8").write(text)
PY
fi

num=""
count=1
if command -v gh >/dev/null 2>&1 && [ -n "${GH_TOKEN:-}" ]; then
  num="$(gh issue list --state open --label "$label" --limit 50 --json number,title \
          --jq '.[] | "\(.number)\t\(.title)"' 2>/dev/null | awk -F'\t' -v t="$title" '$2 == t { print $1; exit }')"
  if [ -n "$num" ]; then
    prev="$(gh issue view "$num" --json body --jq .body 2>/dev/null | grep -oE '누적 실패: [0-9]+' | grep -oE '[0-9]+' | head -n 1)"
    count=$(( ${prev:-1} + 1 ))
  fi
fi

{
  echo "## ${title}"
  echo
  echo "- 마지막 실패: ${now}"
  echo "- 누적 실패: ${count}"
  echo "- 실행 기록: ${run_url}"
  echo "- 이벤트: ${GITHUB_EVENT_NAME:-?} · 커밋 ${sha:0:7}"
  echo
  if [ -n "$detail_file" ] && [ -s "$detail_file" ]; then
    echo "### 요약"
    echo
    echo '```'
    head -c 15000 "$detail_file"
    echo
    echo '```'
    echo
  fi
  echo "### 할 일"
  echo
  echo "1. 위 실행 기록에서 빨간 단계의 로그를 엽니다(비밀값은 *** 로 가려져 있습니다)."
  echo "2. \`[설정 필요]\` 나 \`환경변수 … 가 없습니다\` 가 보이면 docs/SETUP.md 의 해당 절대로 GitHub Secrets 를 고칩니다."
  echo "3. 토큰 만료(invalid_grant, code 190 등)면 로컬에서 \`python -m studio auth <플랫폼>\` 으로 다시 발급해 Secret 을 바꿉니다."
  echo "4. 고친 뒤 Actions 탭에서 이 워크플로를 **Run workflow** 로 다시 돌립니다. 성공하면 이 이슈를 닫습니다."
} > "$body"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$body" >> "$GITHUB_STEP_SUMMARY"
else
  cat "$body"
fi

if ! command -v gh >/dev/null 2>&1 || [ -z "${GH_TOKEN:-}" ]; then
  echo "gh 또는 GH_TOKEN 이 없어 이슈는 남기지 않습니다."
  exit 0
fi
gh label create "$label" --color B60205 --description "정기 자동 실행 실패" >/dev/null 2>&1 || true
if [ -n "$num" ]; then
  gh issue edit "$num" --body-file "$body" >/dev/null && echo "이슈 #${num} 갱신(누적 ${count}회)" || echo "이슈 갱신 실패(권한 issues: write 확인)"
else
  gh issue create --title "$title" --label "$label" --body-file "$body" || echo "이슈 생성 실패(권한 issues: write 확인)"
fi
exit 0
