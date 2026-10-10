---
description: 공인중개사 문제은행 주간 신규 문항 생성·검증. 법령 원문을 먼저 받아 보며 OX·5지선다·빈칸·순서·계산 문항을 쓰고, add → verify(법령 원문 자동 대조) → crosscheck(정답 없이 두 번 풀기) → 통과 문항만 콘텐츠·앱에 쓴다. /weekly-publish 4단계가 부른다.
argument-hint: "[--week 2026-W42] [--count 156] [--subject BRK] [--watch-only]"
allowed-tools: Bash(python -m studio *) Bash(python3 -m studio *) Bash(python -m studio.bank *) Read Write Edit Glob Grep
---

# /question-bank

인자: `$ARGUMENTS`

- `--week W` : 기준 주차(없으면 `python -m studio weekly status` 의 이번 묶음, 그것도 없으면 이번 ISO 주).
- `--count N` : 이번 주 초안 수를 직접 정한다(아래 1단계 계산을 덮어씀).
- `--subject` : 한 과목만(코드 RE CIV BRK PUB REG TAX).
- `--watch-only` : 0단계(개정 감지)와 보고만 한다.

> 명령 이름: `python -m studio bank …`. 아직 `studio/cli.py` 에 bank 가 등록되지 않은 환경이면 같은 인자로 `python -m studio.bank …` 를 쓴다.

## 지켜야 할 것

- 사람에게 묻지 않는다. 판단은 `docs/CONTENT_GUIDE.md`(특히 5·6·7장)와 이 문서로 한다.
- **법령 원문을 먼저 받고, 원문을 보면서 쓴다.** 기억으로 쓴 숫자·요건은 쓰지 않는다. 원문을 받을 수 없으면 그 문항을 만들지 않는다.
- **기출 원문·보기 금지.** 시험지 문장, 시중 교재·강사 문장을 옮기지 않는다. 출제 포인트(무엇이 자주 나오는가)만 참고해 문장은 새로 쓴다.
- `channel.yaml` 의 `rules.banned_phrases`(합격 보장·적중 등)와 "공식·큐넷 제공·국가공인" 같은 사칭 표현을 쓰지 않는다. 예외가 있는 규정에 "항상·절대"로 단정하는 지문은 '예외누락' 함정 OX 의 **틀린 지문**으로만 쓴다.
- 판례 문항은 사건번호·선고일이 API 로 확인되는 것만 쓴다.
- 정답을 모르는(원문만으로 정해지지 않는) 문항은 버린다. 해석이 갈리는 지문, 학설 대립 지문은 쓰지 않는다.

## 0. 개정 감지 (매주 먼저)

`python -m studio bank watch-laws`
- retired 가 생기면 `content/bank/law_changes/<날짜>.md` 를 읽는다. "재검증 후보(인용 부분 그대로)"는 바뀐 원문을 다시 받아(`bank law … --refresh`) 내용이 여전히 맞는지 보고, 맞으면 `basis.effective_date` 를 새 시행일로 고쳐 `bank update`, 틀리면 새 원문에 맞게 지문·정답·해설을 고쳐 `bank update` 한다.
- "사용처"에 적힌 콘텐츠는 /weekly-publish 실행 보고서의 "정정 필요" 목록에 넣는다(CONTENT_GUIDE 7장 8번: 24시간 안에 정정).
- `--watch-only` 면 여기서 보고하고 끝낸다.

## 1. 목표 수 정하기

1. `python -m studio bank stats` 로 과목별 verified 수와 지난 4주 신규 수를 본다.
2. 이번 주 콘텐츠 필요 문항(원고 장면에 들어갈 verified 문항 수)을 `content/weeks/W/plan.json` 으로 센다.

   | 콘텐츠 | 필요 문항 |
   |---|---|
   | 쇼츠 형식 1 함정 OX · 6 판례 · 11 생활법률 | 편당 OX 1 |
   | 쇼츠 형식 2 숫자 빈칸표 | 편당 blank 5~8 (같은 표의 행) |
   | 쇼츠 형식 3 기간 타임라인 | 편당 mcq 1 |
   | 쇼츠 형식 4 절차 흐름도 · 5 순서 맞추기 | 편당 order 1 |
   | 쇼츠 형식 7 계산 단계 | 편당 calc 1 |
   | 쇼츠 형식 8 혼동 쌍 비교표 | 편당 OX 6 |
   | 카드뉴스 | 세트당 OX 1 |
   | 블로그 | 편당 OX 10 |
   | 롱폼 A(120 OX) / B(빈칸 30) / C(판례 15선 × 함정 지문 2) / D(모의 40) | 120 / 30 / 30 / 40 |

3. 재사용 가능 수 = `python -m studio bank list --ready --unused --json` 의 개수(과목·유형이 맞는 것).
4. **초안 수 N = max(⌈(필요 − 재사용 가능)⁺ × 1.3⌉, ⌈120 × 1.3⌉ = 156)**. 120 은 앱 은행 성장 목표(주간 verified 120문항), ×1.3 은 검증 탈락분이다. `--count` 가 있으면 그 값을 쓴다.

## 2. 배분

- **과목**(기본): 민법 25% · 중개사법 20% · 공법 20% · 학개론 15% · 공시법 10% · 세법 10%. verified 가 가장 적은 과목에 +5%p, 가장 많은 과목에서 −5%p. 이번 주 콘텐츠가 요구하는 과목·유형은 먼저 채운다.
- **유형**: OX 50% · mcq 25% · blank 15% · order 5% · calc 5%(세법·학개론 위주).
- **함정 유형 다양화**: 한 주에 한 함정 유형이 30%를 넘지 않게. 과목마다 3가지 이상. 숫자바꿈은 35% 이하. "기타"는 10% 이하.
- **단원**(unit) 이름은 아래 목록의 표기를 그대로 쓴다(통계·약점 진단이 단원별로 묶인다).
  - RE: 부동산학 총론 · 부동산경제론 · 부동산시장론 · 부동산정책론 · 부동산투자론 · 부동산금융론 · 개발·관리론 · 감정평가론
  - CIV: 민법총칙 · 물권법 · 계약법 · 주택임대차보호법 · 상가건물 임대차보호법 · 집합건물법 · 가등기담보법 · 부동산실명법
  - BRK: 총칙 · 공인중개사 제도 · 중개사무소 개설등록 · 중개업무 · 중개계약 · 손해배상책임 · 금지행위 · 교육·감독·벌칙 · 부동산거래신고 · 중개실무
  - PUB: 국토계획법 · 도시개발법 · 도시정비법 · 건축법 · 주택법 · 농지법
  - REG: 공간정보관리법 · 부동산등기법
  - TAX: 조세총론 · 취득세 · 등록면허세 · 재산세 · 종합부동산세 · 양도소득세

## 3. 원문 받기 → 문항 쓰기

과목·단원마다:

1. 조문 목록: `python -m studio bank law --name 공인중개사법` (조문 번호·제목).
2. 원문: `python -m studio bank law --name 공인중개사법 --article 제33조` — 현행 시행일, 시행예정 개정, 근거 배지가 함께 나온다. 숫자가 "대통령령으로 정한다"면 `--decree` 로 시행령을 받는다(그 조문을 basis 에 따로 단다).
3. 원문을 보며 문항을 쓴다. 규칙:
   - `stem`: ox 는 진술문(물음표 없이). mcq 발문은 "옳은 것은?" 또는 "옳지 않은 것은?"을 꼭 넣는다(검증기가 보기 숫자를 대조하는 기준). blank 는 빈칸을 `( )` 로 표시. order 는 "다음 절차를 순서대로 나열한 것은?" + choices 에 단계(섞어서), answer 는 올바른 순서의 인덱스 목록.
   - `answer`: ox `true/false`, mcq 0부터 인덱스, blank 문자열(법령 표기 그대로, 예: `3개월`), order 인덱스 목록, calc 숫자.
   - `explanation`: 맞는 규칙을 원문 숫자·표기 그대로 한두 문장. 틀린 지문이면 무엇이 틀렸는지(예: "5년이 아니라 3년"). 계산 문항은 마지막 줄에 `calc: <식>`(숫자·+-*/()·floor/ceil/round/min/max/abs/trunc, 쉼표 없는 숫자) — 세율 같은 법정 숫자는 본문에 `1천분의 40` 처럼 조문 표기로 함께 적는다.
   - `basis`: `{"kind":"law","law_name":"공인중개사법","article":"제33조 제1항 제6호","effective_date":"<bank law 가 보여 준 현행 시행일>","quote":"<원문에서 그대로 복사한 연속 구간 8자 이상>"}`. 지문의 숫자가 다른 조문(시행령 등)에 있으면 그 조문도 basis 에 추가한다. 판례는 `{"kind":"precedent","case_no":"2019다12345","decided_at":"YYYY-MM-DD","quote":"판결요지 일부"}`. 이론 계산(학개론)은 `{"kind":"calc","note":"직접환원법: 가액 = 순영업소득 ÷ 환원이율"}`.
   - `trap`: 숫자바꿈·주체바꿈·예외누락·기간혼동·요건혼동·용어혼동·기타 중 실제로 노린 함정. 숫자바꿈 OX 는 지문 숫자가 원문과 달라야 한다(검증기가 경고).
   - `difficulty` 1~5, `free`: 무료 덱(하루 20문항·영상 연동 덱)에 넣을 문항만 true — 과목마다 쉬운 문항(1~2) 위주로 주 20개 안팎.
4. 한 번에 과목 하나씩 JSON 배열 파일로 저장한다: `out/bank-inbox/W-<과목코드>-<n>.json` (id·status 는 쓰지 않는다).

## 4. 추가 → 검증

1. `python -m studio bank add --file out/bank-inbox/W-BRK-1.json`
   - 거부 사유: 중복(지문 3-gram 유사도 ≥ 0.8) → 다른 쟁점으로 새로 쓴다. 근거 누락·형식 오류·금지 표현 → 고쳐서 그 문항만 다시 add.
2. `python -m studio bank verify` (기본: draft 전부). 결과는 `out/bank-verify/latest.json`.
   - 반려(rejected) 사유별 처리:
     - 인용문 원문 불일치 → 출력된 "가장 비슷한 구간"을 원문에서 확인해 quote 를 원문 그대로 고친다. "다른 항·호에 있음"이면 article 을 고친다.
     - 숫자 대조 실패 → 숫자가 원문과 다르면 문항이 틀린 것이다. 원문 숫자로 고치거나, 그 숫자가 있는 조문(시행령 등)을 basis 에 추가한다.
     - 시행일·시험일 실패 → effective_date 를 현행 시행일로. 시험일 뒤 시행 버전은 쓰지 않는다.
     - 계산 불일치 → 식과 정답 중 틀린 쪽을 고친다.
   - 고친 문항은 id 를 넣어 `python -m studio bank update --file <파일>` → 다시 `bank verify --id <id>`. **한 문항에 3번 반려되면 버린다**(rejected 로 둔다).
   - 보류(종료 코드 3): `LAW_API_OC` 가 없거나 API 장애 — 문항 잘못이 아니다. 실행 보고서에 적고 다음 단계로 가지 않는다.
   - 경고는 통과지만 읽는다: "위임 조문을 basis 에 추가하세요"는 추가하고 update 한다. "시험 전 개정 예정"은 앱에 경고 배지가 붙는다 — 콘텐츠에 쓸 때는 개정 사실을 함께 말하거나 다른 문항을 쓴다.

## 5. 독립 정답 확인(crosscheck) — verified 문항마다 2번

1. `python -m studio bank show --id <id> --blind` — 정답·해설·근거 없이 지문과 보기만 나온다.
2. 저장된 정답을 보지 않은 상태로, 필요하면 `bank law` 로 원문을 다시 읽고 처음부터 푼다. 답을 기록한다:
   `python -m studio bank crosscheck --id <id> --answer <답> --by claude`
   - 답 형식: ox `O`/`X` · mcq **동그라미 숫자 `①~⑤`**(또는 `3번`; 맨 숫자는 0부터 인덱스로 읽으니 쓰지 않는다) · blank 원문 표기 낱말 · order `2,0,1,3`(0부터) · calc 숫자.
3. 두 번째 풀이는 첫 번째 답과 풀이를 참고하지 않고 지문을 다시 읽고 새로 푼다(가능하면 다른 순서로 문항을 섞어 푼다).
4. 저장된 정답과 다르면 문항은 즉시 rejected 가 된다 → 원문을 다시 읽고 정답·지문 중 틀린 쪽을 고쳐 update → verify → crosscheck 2번. 정답이 하나로 정해지지 않는 지문이면 버린다.
5. calc 문항은 식 검산으로 갈음한다(crosscheck 면제).

## 6. 콘텐츠에 쓰기 · 앱 팩

- 원고에는 `python -m studio bank list --ready --unused --subject <과목> --json` 으로 고른 문항만 쓴다. 장면 `params.bank_id` 에 id, `params.basis` 에 `badges[0]`. 발행 뒤 `python -m studio bank mark-used --asset <콘텐츠 ID> --items <Q-…> …`.
- 주 1회(검증·교차확인이 끝난 뒤): `python -m studio bank export` → `python -m studio bank upload`(리허설은 `--dry-run`). 앱 팩에는 verified + 교차확인 통과 문항만 들어간다.

## 7. 보고

마지막에 `python -m studio bank stats` 를 실행하고, 다음을 5줄 안팎으로 출력한다(/weekly-publish 실행 보고서에 그대로 붙인다):
초안 N · 추가 · verified · 반려(주요 사유 2개) · 교차확인 통과 · retired(개정) · 과목별 verified 증가 · 함정 유형 분포에서 30% 넘는 유형 · 사람이 할 일(키 없음·API 장애 등).
