// 이 파일은 scripts/pull-seed.mjs 가 만든다. 직접 고치지 말고 `npm run seed:pull` 을 다시 실행한다.
// 앱에 내장되는 시드 팩(첫 실행·오프라인용). Metro 는 require 경로가 정적이어야 번들에 넣는다.
import type { SeedBundle } from '@/core/pack/sync';

export const SEED: SeedBundle = {
  manifest: require('../../assets/seed/manifest.json'),
  files: {
    'RE.json': require('../../assets/seed/RE.json'),
    'CIV.json': require('../../assets/seed/CIV.json'),
    'BRK.json': require('../../assets/seed/BRK.json'),
    'PUB.json': require('../../assets/seed/PUB.json'),
    'REG.json': require('../../assets/seed/REG.json'),
    'TAX.json': require('../../assets/seed/TAX.json'),
  },
};
