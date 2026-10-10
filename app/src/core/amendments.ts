/**
 * 개정 알림 목록 = 매니페스트 extra.amendments(있으면) + 문항 alerts(upcoming_amendment)에서 모은 것.
 * 문항 alerts 는 studio 검증 파이프라인이 "시험 전에 시행될 개정"을 찾으면 붙인다.
 * 제목·시행일은 무료, 관련 문항 목록·비교 상세는 유료 화면에서 보인다.
 */
import type { Amendment, LoadedPack } from './pack/types';
import { seedFromString } from './random';
import { dotDate } from './time';

export function collectAmendments(pack: LoadedPack): Amendment[] {
  const out: Amendment[] = [...(pack.manifest.extra?.amendments ?? [])];
  const byKey = new Map<string, Amendment>();
  for (const it of pack.items) {
    for (const a of it.alerts ?? []) {
      if (a.kind !== 'upcoming_amendment' || !a.law_name || !a.effective_date) continue;
      const key = `${a.law_name}|${a.article}|${a.effective_date}`;
      let e = byKey.get(key);
      if (!e) {
        e = {
          id: `alert-${seedFromString(key).toString(16)}`,
          title: `${a.law_name} ${a.article} 개정 · ${dotDate(a.effective_date)} 시행 예정`.replace(/\s+/g, ' '),
          law_name: a.law_name,
          effective_date: a.effective_date,
          summary: `시험 전에 시행되는 개정이 확인된 조문입니다. 관련 문항은 ${dotDate(a.effective_date)} 시행 기준으로 다시 검증합니다.`,
          basis: [{ kind: 'law', law_name: a.law_name, article: a.article, effective_date: a.effective_date }],
          affected_item_ids: [],
        };
        byKey.set(key, e);
      }
      e.affected_item_ids!.push(it.id);
    }
  }
  const known = new Set(out.map((a) => a.id));
  for (const e of byKey.values()) if (!known.has(e.id)) out.push(e);
  return out.sort((a, b) =>
    (b.published_at ?? b.effective_date).localeCompare(a.published_at ?? a.effective_date) || a.title.localeCompare(b.title),
  );
}
