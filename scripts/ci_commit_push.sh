#!/usr/bin/env bash
# GitHub Actions 에서 바뀐 결과 파일(content/ · archive/ · reports/ …)을 봇 계정으로 커밋·푸시한다.
#
#   bash scripts/ci_commit_push.sh "<커밋 메시지>" <경로> [<경로> ...]
#
# - 지정한 경로만 스테이징한다(.gitignore 를 따르므로 out/ · .secrets/ · 영상 파일은 올라가지 않는다).
# - 바뀐 것이 없으면 아무것도 하지 않고 0.
# - 푸시가 거절되면(다른 실행이 먼저 푸시) 원격을 받아 rebase 한 뒤 다시 시도한다(PUSH_RETRIES, 기본 5회).
# - rebase 충돌이면 중단하고 1 — 두 실행이 같은 파일을 동시에 고친 경우다. 실패 알림 단계가 이슈를 남긴다.
# 환경변수: GITHUB_TOKEN(있으면 origin 인증을 이 토큰으로 맞춘다), GITHUB_REF_NAME(푸시할 브랜치),
#           PUSH_RETRIES(기본 5), PUSH_RETRY_DELAY(재시도 간격 배수 초, 기본 5)
set -euo pipefail

msg="${1:?커밋 메시지를 첫 인자로 주세요}"
shift
if [ "$#" -eq 0 ]; then
  echo "커밋할 경로를 하나 이상 주세요." >&2
  exit 2
fi
retries="${PUSH_RETRIES:-5}"
branch="${GITHUB_REF_NAME:-$(git rev-parse --abbrev-ref HEAD)}"
server="${GITHUB_SERVER_URL:-https://github.com}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# claude-code-action 은 origin 주소를 자기 토큰으로 바꿔 둔다(긴 실행 뒤에는 만료됐을 수 있음) → 작업 토큰으로 되돌린다.
if [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@${server#https://}/${GITHUB_REPOSITORY}.git"
fi

existing=()
for p in "$@"; do
  if [ -e "$p" ] || git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then
    existing+=("$p")
  fi
done
if [ "${#existing[@]}" -eq 0 ]; then
  echo "커밋할 경로가 아직 없습니다($*) — 건너뜁니다."
  exit 0
fi

git add -A -- "${existing[@]}"
if git diff --cached --quiet; then
  echo "바뀐 파일이 없습니다 — 커밋하지 않습니다."
  exit 0
fi

run_url="${server}/${GITHUB_REPOSITORY:-local}/actions/runs/${GITHUB_RUN_ID:-0}"
git commit -q -m "$msg" -m "자동 실행: ${GITHUB_WORKFLOW:-local} #${GITHUB_RUN_NUMBER:-0} — ${run_url}"
git --no-pager show --stat --format='%h %s' HEAD | head -n 40

for i in $(seq 1 "$retries"); do
  if git push origin "HEAD:refs/heads/${branch}"; then
    echo "푸시 완료 → ${branch}"
    exit 0
  fi
  echo "푸시 거절 — 원격 변경을 받아 rebase 후 다시 시도합니다 (${i}/${retries})."
  sleep $((i * ${PUSH_RETRY_DELAY:-5}))
  git fetch -q origin "${branch}"
  if ! git rebase --autostash "origin/${branch}"; then
    echo "::error::rebase 충돌 — 다른 실행이 같은 파일을 동시에 고쳤습니다. 충돌 파일:"
    git diff --name-only --diff-filter=U || true
    git rebase --abort || true
    exit 1
  fi
done
echo "::error::${retries}번 시도해도 푸시하지 못했습니다(브랜치 보호 규칙·권한 contents: write 를 확인하세요)."
exit 1
