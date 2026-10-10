#!/usr/bin/env bash
# Supabase 백엔드 배포 — 처음 한 번, 그리고 supabase/ 아래를 고쳤을 때.
#
#   bash scripts/supabase_deploy.sh <project-ref>
#
# 순서: 프로젝트 연결(link) → 마이그레이션 적용(db push: 테이블·RLS·media 버킷)
#       → 함수 비밀값(supabase/functions/.env 가 있으면 secrets set) → 함수 배포(go, revenuecat-webhook)
# 준비: Node.js(npx) 또는 supabase CLI, 그리고 `supabase login`(또는 npx supabase login) 한 번.
# project-ref: 대시보드 주소 https://supabase.com/dashboard/project/<project-ref> 의 마지막 부분.
set -euo pipefail

ref="${1:-${SUPABASE_PROJECT_REF:-}}"
if [ -z "$ref" ]; then
  echo "사용법: bash scripts/supabase_deploy.sh <project-ref>" >&2
  exit 2
fi
cd "$(dirname "$0")/.."

if [ -n "${SUPABASE_BIN:-}" ]; then
  sb=("$SUPABASE_BIN")
elif command -v supabase >/dev/null 2>&1; then
  sb=(supabase)
else
  sb=(npx --yes supabase)
fi

echo "== 1/4 프로젝트 연결 (DB 비밀번호를 물으면 프로젝트 만들 때 정한 값을 넣습니다)"
"${sb[@]}" link --project-ref "$ref"

echo "== 2/4 마이그레이션 적용 (supabase/migrations/*.sql)"
"${sb[@]}" db push

echo "== 3/4 함수 비밀값"
if [ -f supabase/functions/.env ]; then
  "${sb[@]}" secrets set --env-file supabase/functions/.env --project-ref "$ref"
else
  echo "[주의] supabase/functions/.env 가 없어 건너뜁니다. supabase/functions/.env.example 을 복사해 값을 채운 뒤 다시 실행하세요."
fi

echo "== 4/4 함수 배포 (공개 호출이라 JWT 검사 끔 — supabase/config.toml 과 같음)"
"${sb[@]}" functions deploy go --no-verify-jwt --project-ref "$ref"
"${sb[@]}" functions deploy revenuecat-webhook --no-verify-jwt --project-ref "$ref"

echo
echo "완료."
echo "  추적 링크 기본 주소(channel.yaml app.link_base): https://${ref}.supabase.co/functions/v1/go"
echo "  RevenueCat 웹훅 URL:                           https://${ref}.supabase.co/functions/v1/revenuecat-webhook"
echo "  확인: curl -sI 'https://${ref}.supabase.co/functions/v1/go?s=test&m=setup' | head -n 5"
