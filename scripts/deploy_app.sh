#!/usr/bin/env bash
# 앱 배포(/deploy): 검사 → 네이티브 지문(fingerprint) → 같은 지문의 완료 빌드가 있으면 OTA 업데이트,
# 없으면(또는 --force-build) 스토어 빌드 + 자동 제출.
#
# 사용법:
#   scripts/deploy_app.sh ["메시지"] [--dry-run] [--force-build] [--platform all|android|ios] [--wait]
#                         [--skip-checks] [--allow-unverified-seed]
#
#   --dry-run                무엇을 할지 출력만(빌드·업데이트를 실행하지 않음). EXPO_TOKEN 이 있으면
#                            지문·빌드 조회(읽기 전용)까지 해서 실제 판정을 보여 준다.
#   --force-build            지문이 같아도 새 스토어 빌드
#   --platform               기본 all
#   --wait                   빌드가 끝날 때까지 기다림(기본은 걸어 두고 바로 끝냄 → CI 시간 절약)
#   --skip-checks            tsc·jest 생략(급한 재시도용)
#   --allow-unverified-seed  근거 대조 전 시드 팩이 들어 있어도 진행(내부 테스트 빌드 전용)
#
# 필요: Node 22, app/node_modules(없으면 npm ci), EXPO_TOKEN(https://expo.dev/settings/access-tokens)
#       또는 로컬에서 `eas login` 한 상태. iOS 자동 제출은 app/eas.json submit.production.ios.ascAppId 필요.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/app"

MESSAGE=""
DRY_RUN=0
FORCE_BUILD=0
PLATFORM="all"
WAIT=0
SKIP_CHECKS=0
ALLOW_UNVERIFIED=0

die() {
  echo "" >&2
  echo "[배포 중단] $*" >&2
  exit 1
}
info() { echo "▶ $*"; }
plan() { echo "  (계획) $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --force-build) FORCE_BUILD=1 ;;
    --wait) WAIT=1 ;;
    --skip-checks) SKIP_CHECKS=1 ;;
    --allow-unverified-seed) ALLOW_UNVERIFIED=1 ;;
    --platform)
      [[ $# -ge 2 ]] || die "--platform 다음에 all|android|ios 중 하나를 적으세요."
      PLATFORM="$2"
      shift
      ;;
    --platform=*) PLATFORM="${1#*=}" ;;
    -h | --help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    --*) die "알 수 없는 옵션: $1 (도움말: --help)" ;;
    *)
      [[ -z "$MESSAGE" ]] || die "메시지는 하나만 줄 수 있습니다. 띄어쓰기가 있으면 따옴표로 감싸세요."
      MESSAGE="$1"
      ;;
  esac
  shift
done

case "$PLATFORM" in
  all) PLATFORMS=(android ios) ;;
  android | ios) PLATFORMS=("$PLATFORM") ;;
  *) die "--platform 은 all, android, ios 중 하나여야 합니다(받은 값: $PLATFORM)." ;;
esac

if [[ -z "$MESSAGE" ]]; then
  MESSAGE="$(git -C "$ROOT" log -1 --pretty=%s 2>/dev/null || true)"
  [[ -n "$MESSAGE" ]] || MESSAGE="app update $(date -u +%Y-%m-%dT%H:%MZ)"
fi

[[ -d "$APP" ]] || die "app/ 폴더가 없습니다($APP)."
command -v node >/dev/null || die "node 가 없습니다. Node 22 를 설치하세요."
command -v npm >/dev/null || die "npm 이 없습니다."
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 20 ]] || die "Node 20 이상이 필요합니다(지금 $(node -v)). Node 22 를 권장합니다."

cd "$APP"

# eas: 설치돼 있으면 그대로(expo-github-action 이 넣어 줌), 없으면 npx 로 24.x 를 받는다
if command -v eas >/dev/null 2>&1; then
  EAS=(eas)
else
  EAS=(npx --yes eas-cli@^24)
fi

echo "== 함정노트 앱 배포 =="
echo "  플랫폼: ${PLATFORMS[*]} · 메시지: $MESSAGE"
[[ $DRY_RUN -eq 1 ]] && echo "  모드: 리허설(--dry-run) — 빌드·업데이트를 실행하지 않습니다"

# 1) 의존성
if [[ ! -d node_modules ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    plan "npm ci (node_modules 없음)"
  else
    info "npm ci"
    npm ci --no-audit --no-fund || die "npm ci 실패. package-lock.json 이 package.json 과 맞는지 확인하세요."
  fi
fi

# 2) 시드 팩 검사: 근거 대조 전 점검용 시드가 남아 있으면 배포하지 않는다
SEED_UNVERIFIED="$(node -e "try{const m=require('./assets/seed/manifest.json');process.stdout.write(m.extra&&m.extra.unverified===true?'1':'0')}catch(e){process.stdout.write('missing')}")"
if [[ "$SEED_UNVERIFIED" == "missing" ]]; then
  die "app/assets/seed/manifest.json 이 없습니다. npm run seed:pull 로 시드 팩을 만드세요."
elif [[ "$SEED_UNVERIFIED" == "1" ]]; then
  if [[ $ALLOW_UNVERIFIED -eq 1 ]]; then
    echo "  경고: 근거 대조 전 시드 팩으로 진행합니다(--allow-unverified-seed). 스토어 출시용으로 쓰지 마세요."
  elif [[ $DRY_RUN -eq 1 ]]; then
    echo "  경고: 근거 대조 전 시드 팩이 들어 있습니다 — 실제 배포라면 여기서 멈춥니다(npm run seed:pull 필요)."
  else
    die "앱에 근거 대조 전 점검용 시드 팩이 들어 있습니다.
  먼저 검증된 팩으로 바꾸세요:  python -m studio bank export  →  (cd app && npm run seed:pull)
  내부 테스트용이면 --allow-unverified-seed 를 붙이세요."
  fi
fi

# 3) 검사
if [[ $SKIP_CHECKS -eq 1 ]]; then
  echo "  검사 생략(--skip-checks)"
elif [[ $DRY_RUN -eq 1 ]]; then
  plan "npx tsc --noEmit"
  plan "npx jest --ci"
else
  info "타입 검사(tsc)"
  npx tsc --noEmit || die "타입 오류가 있습니다. 위 오류를 고친 뒤 다시 실행하세요."
  info "단위 테스트(jest)"
  npx jest --ci || die "테스트가 실패했습니다. 위 실패 내용을 고친 뒤 다시 실행하세요."
fi

# 4) 프로젝트 ID(app.config.ts 의 updates.url·extra.eas.projectId). 로컬은 app/.env 도 읽는다
if [[ -z "${EAS_PROJECT_ID:-}" && -f .env ]]; then
  EAS_PROJECT_ID="$(sed -n 's/^EAS_PROJECT_ID=//p' .env | tail -1 | tr -d '"'"'"' \r')"
  export EAS_PROJECT_ID
fi
if [[ -z "${EAS_PROJECT_ID:-}" ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  경고: EAS_PROJECT_ID 가 없습니다 — 실제 배포라면 여기서 멈춥니다."
  else
    die "EAS_PROJECT_ID 가 없습니다. app/ 에서 'npx eas-cli init' 으로 프로젝트를 만든 뒤 그 ID 를
  - GitHub: Settings → Secrets and variables → Actions → Variables 에 EAS_PROJECT_ID
  - EAS 서버: npx eas-cli env:create --name EAS_PROJECT_ID --value <ID> --environment production --visibility plaintext
  - 로컬: app/.env 에 EAS_PROJECT_ID=<ID>
  로 넣으세요(OTA 업데이트 주소가 이 값으로 만들어집니다)."
  fi
fi

# 5) 인증
HAVE_AUTH=0
if [[ -n "${EXPO_TOKEN:-}" ]]; then
  HAVE_AUTH=1
elif "${EAS[@]}" whoami >/dev/null 2>&1; then
  HAVE_AUTH=1
fi
if [[ $HAVE_AUTH -eq 0 ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  참고: EXPO_TOKEN 이 없어 지문·빌드 조회를 건너뜁니다. 실제 배포에는 필요합니다."
  else
    die "EXPO_TOKEN 이 없습니다.
  https://expo.dev/settings/access-tokens 에서 토큰을 만들어
  - GitHub Actions: 저장소 Settings → Secrets → EXPO_TOKEN
  - 로컬: export EXPO_TOKEN=... 또는 npx eas-cli login"
  fi
fi

# 6) 지문 → 같은 지문의 완료 빌드가 있는지
json_field() { node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const j=JSON.parse(s);$1}catch(e){process.exit(3)}})"; }

NEED_BUILD=()
CAN_UPDATE=()
UNKNOWN=()
for P in "${PLATFORMS[@]}"; do
  if [[ $HAVE_AUTH -eq 0 ]]; then
    UNKNOWN+=("$P")
    continue
  fi
  info "[$P] 네이티브 지문 계산"
  FP_JSON="$("${EAS[@]}" fingerprint:generate --platform "$P" --environment production --build-profile production --json --non-interactive)" ||
    die "[$P] eas fingerprint:generate 실패. app/ 에서 'npx eas-cli fingerprint:generate --platform $P' 를 직접 실행해 원인을 확인하세요."
  HASH="$(printf '%s' "$FP_JSON" | json_field "process.stdout.write(String(j.hash||''))")" || die "[$P] 지문 JSON 을 읽지 못했습니다: $FP_JSON"
  [[ -n "$HASH" ]] || die "[$P] 지문 값이 비어 있습니다."
  echo "  지문: $HASH"
  if [[ $FORCE_BUILD -eq 1 ]]; then
    NEED_BUILD+=("$P")
    continue
  fi
  BUILDS_JSON="$("${EAS[@]}" build:list --platform "$P" --status finished --build-profile production --fingerprint-hash "$HASH" --limit 1 --json --non-interactive)" ||
    die "[$P] eas build:list 실패."
  COUNT="$(printf '%s' "$BUILDS_JSON" | json_field "process.stdout.write(String(Array.isArray(j)?j.length:0))")" || COUNT=0
  if [[ "$COUNT" -gt 0 ]]; then
    echo "  같은 지문의 완료 빌드 있음 → OTA 업데이트로 충분"
    CAN_UPDATE+=("$P")
  else
    echo "  같은 지문의 완료 빌드 없음 → 새 스토어 빌드 필요(네이티브 변경)"
    NEED_BUILD+=("$P")
  fi
done

platform_arg() {
  # 두 플랫폼이면 all, 하나면 그 이름
  if [[ $# -eq 2 ]]; then echo all; else echo "$1"; fi
}

UPDATE_CMD=()
BUILD_CMD=()
if [[ ${#CAN_UPDATE[@]} -gt 0 ]]; then
  UPDATE_CMD=("${EAS[@]}" update --channel production --environment production --message "$MESSAGE" --platform "$(platform_arg "${CAN_UPDATE[@]}")" --non-interactive)
fi
if [[ ${#NEED_BUILD[@]} -gt 0 ]]; then
  BUILD_CMD=("${EAS[@]}" build --platform "$(platform_arg "${NEED_BUILD[@]}")" --profile production --auto-submit --message "$MESSAGE" --non-interactive)
  if [[ $WAIT -eq 1 ]]; then BUILD_CMD+=(--wait); else BUILD_CMD+=(--no-wait); fi
fi

# iOS 자동 제출에는 App Store Connect 앱 ID 가 필요
if printf '%s\n' "${NEED_BUILD[@]:-}" | grep -qx ios; then
  ASC="$(node -p "((require('./eas.json').submit||{}).production||{}).ios?.ascAppId||''")"
  if [[ -z "$ASC" || "$ASC" == "0000000000" ]]; then
    MSG_ASC="app/eas.json 의 submit.production.ios.ascAppId 를 App Store Connect 앱 ID(숫자)로 바꾸세요."
    if [[ $DRY_RUN -eq 1 ]]; then echo "  경고: $MSG_ASC"; else die "$MSG_ASC"; fi
  fi
fi

echo ""
echo "== 판정 =="
[[ ${#CAN_UPDATE[@]} -gt 0 ]] && echo "  OTA 업데이트: ${CAN_UPDATE[*]}"
[[ ${#NEED_BUILD[@]} -gt 0 ]] && echo "  스토어 빌드+제출: ${NEED_BUILD[*]}"
if [[ ${#UNKNOWN[@]} -gt 0 ]]; then
  echo "  판정 불가(인증 없음): ${UNKNOWN[*]} — 다음 둘 중 하나를 실행합니다"
  plan "같은 지문의 빌드가 있으면: ${EAS[*]} update --channel production --environment production --message \"$MESSAGE\" --platform $PLATFORM --non-interactive"
  plan "없으면: ${EAS[*]} build --platform $PLATFORM --profile production --auto-submit --non-interactive --no-wait"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  [[ ${#UPDATE_CMD[@]} -gt 0 ]] && plan "${UPDATE_CMD[*]}"
  [[ ${#BUILD_CMD[@]} -gt 0 ]] && plan "${BUILD_CMD[*]}"
  echo ""
  echo "리허설 끝. 실제로 하려면 --dry-run 을 빼고 다시 실행하세요."
  exit 0
fi

if [[ ${#UPDATE_CMD[@]} -gt 0 ]]; then
  info "OTA 업데이트 게시"
  "${UPDATE_CMD[@]}" || die "eas update 실패. 위 로그를 확인하세요(채널 production, environment production)."
fi
if [[ ${#BUILD_CMD[@]} -gt 0 ]]; then
  info "스토어 빌드 시작(+자동 제출)"
  "${BUILD_CMD[@]}" || die "eas build 실패. 첫 빌드라면 로컬에서 한 번 'npx eas-cli build --platform <p> --profile production' 으로 자격 증명을 만드세요."
fi

echo ""
echo "완료. 진행 상황: https://expo.dev (프로젝트 → Builds / Updates)"
