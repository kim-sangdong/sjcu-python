---
description: 앱을 고친 뒤 한 줄로 배포한다. 네이티브 변경이 없으면 OTA 업데이트, 있으면 스토어 빌드·제출까지 자동으로 고른다.
argument-hint: "[메시지] [--dry-run] [--force-build] [--platform all|android|ios]"
allowed-tools: Bash(bash scripts/deploy_app.sh *) Bash(git status *) Bash(git diff *) Bash(git log *) Read Glob Grep
---

# /deploy

인자: `$ARGUMENTS`

1. `git status` 와 `git diff --stat` 으로 app/ 아래 변경을 확인한다. 커밋되지 않은 변경이 있으면 무엇인지 요약하고, 배포 메시지에 반영한다.
2. `bash scripts/deploy_app.sh <인자 그대로>` 를 실행한다. 이 스크립트가
   - 타입 검사·테스트를 돌리고,
   - 네이티브 지문(fingerprint)을 마지막 스토어 빌드와 비교해,
   - 같으면 `eas update`(OTA, 몇 분 안에 사용자 앱에 반영), 다르면 `eas build --auto-submit`(스토어 심사 필요)을 실행한다.
3. 결과를 요약한다: 어떤 경로(OTA/스토어)로 배포됐는지, 이유, 사용자에게 반영되는 시점, 실패했다면 오류 원인과 다음 조치.
