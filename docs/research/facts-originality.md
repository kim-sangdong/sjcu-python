# 독창성 검사기 설계 조사: 반복·대량생산 콘텐츠 판정 회피 (2026-10-10)

## 0. 검증 상태: 먼저 읽을 것

이번 실행에서는 **플랫폼 정책 원문을 하나도 열어 보지 못했다.**
- WebSearch는 첫 호출부터 쓸 수 없었다. 같은 실행의 여러 에이전트가 함께 쓰는 한도(200회)가 이미 소진된 상태였다.
- WebFetch는 모든 호스트에서 DNS 오류(`ENOTFOUND`)가 났다.
- 프록시를 통한 curl도 `support.google.com`이 조직 정책으로 거부됐다(403). transparency.meta.com, tiktok.com, blog.youtube 등도 연결되지 않았다.
- 접근된 곳은 PyPI와 GitHub raw뿐이다. 그래서 기술 사실(라이브러리·ffmpeg·Meta 오픈소스 해시·YouTube API 필드)만 원문으로 확인했다.

각 주장에 붙인 표시는 다음과 같다.

| 표시 | 의미 |
|---|---|
| **[검증]** | 이번 실행에서 원문을 직접 내려받아 확인함. URL 표기 |
| **[실험]** | 이번 실행에서 로컬 합성 데이터로 직접 측정함(ffmpeg 6.1.1, Python 3.13) |
| **[기억·미확인]** | 2026-06까지의 학습 지식. 원문 대조를 못 함. 괄호 안 URL은 "확인할 공식 문서"(이 URL 자체도 미확인) |
| **[추정]** | 근거가 약한 설계 판단. 실운영 데이터로 다시 맞춰야 함 |

정책 원문 확인은 사람이 직접 하거나, 검색 한도를 올리고(`CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`) 후속 메시지로 다시 실행해야 한다. 8절에 대조 목록을 두었다.

---

## 1. 플랫폼 정책 조사

### 1.1 YouTube: inauthentic content(구 repetitious content)와 reused content

| 항목 | 내용 | 상태 · 확인처 |
|---|---|---|
| 2025-07-15 개정 | YPP 도움말에 "2025년 7월 15일부터 대량생산·반복 콘텐츠를 더 잘 식별하도록 가이드라인을 갱신한다"는 공지가 붙었다. 이후 정책 이름이 *repetitious content*에서 *inauthentic content*로 바뀌었다. YouTube 크리에이터 담당(Rene Ritchie)은 "오래된 정책의 소폭 개정"이며 리액션·해설 영상은 대상이 아니라고 설명했다. reused content 정책 자체는 바뀌지 않았다. | [기억·미확인] (support.google.com/youtube/answer/1311392) |
| inauthentic 정의 | 대량생산되거나 반복적인 콘텐츠. 영상마다 변화가 거의 없는 **템플릿으로 만든 것처럼 보이는 콘텐츠**, 또는 **대규모로 쉽게 복제할 수 있는 콘텐츠**. 원문 문구가 아니라 기억으로 재구성한 것이다. | [기억·미확인] (같은 URL) |
| 허용되지 않는 예 | (1) 직접 만들지 않은 자료(웹사이트 글·뉴스 피드)를 **읽기만 하는** 콘텐츠 (2) 음정·속도만 바꾸고 원곡과 같은 음원 (3) 교육적 가치·해설·서사가 낮은 반복적·무성의 콘텐츠 (4) 서사·해설·교육적 가치가 거의 없는 **이미지 슬라이드쇼나 스크롤 텍스트** | [기억·미확인] |
| 허용되는 예 | 인트로·아웃트로는 비슷해도 **본문이 다른** 콘텐츠. 비슷한 형식이라도 영상마다 의미 있는 차이가 있는 콘텐츠 | [기억·미확인] |
| reused 정의 | 남의 콘텐츠를 쓸 때는 의미 있는 독창적 해설, 실질적 수정, 교육·오락적 가치를 더해야 한다. 거의 바꾸지 않은 짜깁기, 다른 플랫폼에서 모은 짧은 영상, 여러 번 업로드된 콘텐츠는 불허한다. | [기억·미확인] |
| 적용 단위 | 두 정책 모두 **채널 전체**에 적용된다. YPP 심사자는 채널 주제, 조회수 상위 영상, 최신 영상, 시청 시간 비중이 큰 영상, 메타데이터(제목·썸네일·설명), 정보 탭을 본다. | [기억·미확인] (YPP 심사 도움말, URL 미확인) |
| 별도 정책 | 스팸 정책은 과도하게 게시되거나 반복적인 콘텐츠, 오해를 부르는 메타데이터, 설명란의 과도한 태그를 금지한다. 수익 제한이 아니라 **삭제**까지 갈 수 있다. | [기억·미확인] (support.google.com/youtube/answer/2801973) |
| 해시태그 | 한 영상에 해시태그가 60개를 넘으면 전부 무시된다. | [기억·미확인] (support.google.com/youtube/answer/6390658) |

**AI 생성 콘텐츠와 공개 의무**

- AI 도구 사용 자체는 수익화 결격 사유가 아니다. 판정 기준은 AI 사용 여부가 아니라 대량생산·반복성이다. [기억·미확인]
- 2024-03부터 "변형되거나 합성된 콘텐츠"는 공개해야 한다. 대상은 실제처럼 보이는 경우다.
  - 실존 인물이 하지 않은 말·행동을 하는 것처럼 만든 경우
  - 실제 사건·장소 영상을 바꾼 경우
  - 일어나지 않은 장면을 사실처럼 생성한 경우
- 공개가 필요 없는 경우: 명백히 비현실적인 애니메이션, 색 보정·필터·배경 흐림, 대본·아이디어·자막 생성에 AI를 쓴 경우. [기억·미확인] (support.google.com/youtube/answer/14328491)
- **[검증]** YouTube Data API v3 디스커버리 문서(revision 20261001)에 `VideoStatus.containsSyntheticMedia`(boolean, "Indicates if the video contains altered or synthetic media.")가 있다. 업로드 자동화에서 이 값을 직접 설정할 수 있다. 출처: https://raw.githubusercontent.com/googleapis/google-api-go-client/main/youtube/v3/youtube-api.json
- 제목 100자, 설명 5000바이트, 태그 합계 500자 제한은 위 디스커버리 문서에 적혀 있지 않았다. [기억·미확인]

### 1.2 Meta (Facebook·Instagram)

| 항목 | 내용 | 상태 · 확인처 |
|---|---|---|
| Facebook 2025-07 강화 | 남의 텍스트·사진·영상을 반복 재사용하는 계정은 일정 기간 **수익화 접근을 잃고 배포가 줄어든다**. 중복 영상은 사본의 배포를 줄인다. 원본으로 가는 링크 표시를 시험 중이다. "클립을 이어 붙이거나 워터마크만 추가하는 것은 의미 있는 개선이 아니다." 리액션·트렌드 참여·자기 해석을 더한 콘텐츠는 대상이 아니다. 전문가 대시보드에 게시물 단위로 배포 제한 이유를 보여주는 기능을 추가했다. 단계적으로 시행한다. | [기억·미확인] (creators.facebook.com/blog, about.fb.com/news 2025-07 게시물. 정확한 URL 미확인) |
| Instagram 2024-04 | 추천에서 재게시물을 원본으로 대체한다. 직접 만들지 않은 콘텐츠를 **30일 안에 10회 이상** 반복 게시한 계정은 일정 기간 추천에서 제외한다. 원본 출처 표기 라벨을 붙인다. | [기억·미확인] (about.instagram.com 공지, Adam Mosseri 발표) |
| 타 플랫폼 워터마크 | Reels에 다른 앱(TikTok 등) 워터마크가 있으면 노출이 줄어든다. 2021년 @creators 안내다. | [기억·미확인] |
| 추천 가이드라인 | "독창적이지 않은(unoriginal) 콘텐츠"는 추천 대상에서 빠질 수 있다. 계정 상태 화면에서 추천 적격 여부를 볼 수 있다. | [기억·미확인] (help.instagram.com/313829416281232) |
| Facebook 인스트림 광고 | 정지 영상, 반복(루프) 영상, 슬라이드쇼, 텍스트만 있는 영상은 수익화에서 제한된다. | [기억·미확인] (Facebook 콘텐츠 수익화 정책, URL 미확인) |
| AI 라벨 | 사실적인 AI 영상·음성은 "AI 정보" 라벨로 공개해야 한다. | [기억·미확인] |

### 1.3 TikTok

| 항목 | 내용 | 상태 · 확인처 |
|---|---|---|
| 추천 피드(FYF) 부적격 | 커뮤니티 가이드라인의 FYF 적격성 기준에 "독창성 없는 콘텐츠, 저품질 콘텐츠, QR코드 콘텐츠" 범주가 있다. 의미 있는 편집·추가 없이 재업로드한 콘텐츠와 저품질 콘텐츠는 삭제되지 않아도 **추천에서 제외**된다. | [기억·미확인] (tiktok.com/community-guidelines/en/fyf-standards) |
| AI 생성 콘텐츠 | 사실적인 AI 생성 콘텐츠는 라벨을 붙여야 한다. C2PA 콘텐츠 자격증명(Content Credentials)으로 자동 라벨링도 한다. 라벨이 없으면 제한될 수 있다. | [기억·미확인] (support.tiktok.com, AI-generated content 문서) |
| Creator Rewards | 1분 넘는 **독창적·고품질** 영상만 보상 대상이다. 쇼츠 길이 설계에 영향을 준다. | [기억·미확인] |
| 사전 검사 기능 | TikTok Studio에 게시 전 FYF 적격 여부를 보여주는 "Content Check Lite"가 있다. | [기억·미확인, 존재 여부도 불확실] |

### 1.4 이 사업에서 정책이 뜻하는 것 [추정]

- 수익원이 광고가 아니라 **앱 인앱결제**다. 그래서 가장 큰 위험은 YPP 수익 제한이 아니라 **추천 배포 감소**다. YouTube 추천, Meta 배포 감소, TikTok FYF 제외가 모두 여기에 해당한다. 퍼널 1단계(발견)가 끊긴다.
- 주 14편 쇼츠를 같은 틀로 만드는 구조는 정책 문구 "템플릿 + 변화 거의 없음 + 대규모 복제 가능"에 정면으로 걸린다. 검사기의 핵심은 **배치 내부 다양성**과 **에피소드 간 본문 차별성**이다.
- 자기 원본을 여러 플랫폼에 교차 게시하는 것 자체는 "남의 콘텐츠 재사용"이 아니다. 다만 **타 플랫폼 워터마크를 지운 깨끗한 원본을 플랫폼별로 따로 내보내야** 한다. [기억·미확인]
- 운영자의 기존 네이버 블로그 '디지털 생활비 계산소'와 티스토리 글이 같은 원고에서 나오면 블로그 간 유사문서 문제가 생길 수 있다. 블로그 원고도 같은 텍스트 지표로 네이버 아카이브와 비교하길 권한다. [기억·미확인 / 추정]

---

## 2. 정책 문구를 측정 지표로 번역

| 정책 문구 (출처 상태는 1절) | 지표 ID | 측정하는 것 |
|---|---|---|
| 템플릿 + 영상 간 변화 거의 없음 | S1, T1, V1, C1 | 본문 대본 유사도, 섹션 골격 반복, 썸네일 템플릿, 셋의 결합 |
| 대규모로 쉽게 복제 가능 | B1 배치 지표, S1(숫자 정규화) | 숫자만 바꾼 재탕, 주 14편 내부 중복 군집 |
| 남의 자료를 읽기만 함 | S4 | 조사 출처 원문이 대본에 그대로 들어간 비율 |
| 슬라이드쇼·스크롤 텍스트, 해설 없음 | V3, V4, A1, C2 | 정지 비율, 고유 프레임 비율, 내레이션 비중 |
| 반복적·무성의, 해설·서사 부족 | S3, P1 | 같은 문장 반복, 부가가치 점수 |
| 재사용 콘텐츠(남의 클립) | R1, V2 | 제3자 소재 비중, 소재 재사용률 |
| 오해 소지 메타데이터·과도한 태그 | M1~M4 | 제목·설명·태그 유사도, 규격 위반 |
| 변형·합성 콘텐츠 공개 | D1 | 사실적 AI 소재가 있는데 공개 값이 빠졌는지 |
| 워터마크만 추가는 개선 아님 | (공정 규칙) | 플랫폼별 깨끗한 원본 내보내기 확인 |

---

## 3. 지표별 계산법

### 3.1 공통 텍스트 정규화 (한국어, 형태소 분석 없이)

1. NFC 정규화 후 소문자화.
2. 구두점과 기호를 제거한다.
3. **숫자는 모두 `0`으로 바꾼다.** 숫자만 바꾼 템플릿 재탕을 잡기 위해서다. [추정]
4. 등록된 허용 상용구(인트로·아웃트로·CTA·고정 링크 블록)를 제거한다.
5. 공백을 제거한 뒤 **문자 n-gram**을 만든다. 한글 음절 1개가 1문자이므로 5-gram은 대략 두 어절이다. 띄어쓰기만 바꿔도 피해 가지 못하게 공백을 없앤다.

상용구 제거는 꼭 필요하다. **[실험]** 같은 인트로·아웃트로를 쓴 다른 주제 대본 두 편은 상용구를 지우지 않으면 문장 중복률이 0.33으로 나왔다. 정책상 인트로·아웃트로 공유는 허용되므로 [기억·미확인], 상용구는 비교에서 빼야 한다.

### 3.2 텍스트 지표

| ID | 지표 | 계산 | 라이브러리 |
|---|---|---|---|
| S1 | 본문 대본 유사도 최댓값 | 문자 5-gram 집합의 Jaccard. MinHash(num_perm=256)와 MinHashLSH(threshold=0.3)로 후보만 고른 뒤, 후보와는 정확한 Jaccard를 다시 계산 | datasketch 2.0.0 |
| S2 | 문장 재사용률 | 본문 문장(정규화 후 12자 이상) 중 아카이브에 정확히 있는 문장의 비율. sha1 집합으로 비교 | 표준 라이브러리 |
| S3 | 영상 내부 동일 문장 반복 | 같은 정규화 문장이 2회 이상 나온 문장 수 ÷ 전체 문장 수 | 표준 라이브러리 |
| S4 | 출처 원문 낭독률 | 대본 문자 8-gram 중 조사 출처 원문에 있는 비율(포함도 \|S∩R\|/\|S\|). 출처와 80자 이상 그대로 일치하는 구간 수도 함께 센다 | 표준 라이브러리 |
| S5 | 주제 근접도 | TF-IDF(`analyzer="char_wb"`, `ngram_range=(2,4)`, `sublinear_tf=True`) 코사인. 최근 12주와 비교 | scikit-learn 1.9.1 |
| M1 | 제목 유사도 | 공백을 뺀 문자 2-gram Jaccard (숫자는 `#`으로) | 표준 라이브러리, rapidfuzz 3.14.6 보조 |
| M2 | 제목 골격 반복 | 숫자를 `#`으로 바꾼 뒤 끝 8자가 같은 제목의 배치 내 비율 | 표준 라이브러리 |
| M3 | 설명란 유사도 | 고정 블록(앱 링크·UTM·CTA)을 뺀 본문의 문자 5-gram Jaccard | datasketch |
| M4 | 태그 | 다른 배치 항목과의 태그 집합 Jaccard 평균, 태그 총 길이, 해시태그 수 | 표준 라이브러리 |
| T1 | 구조 템플릿 반복 | 섹션 유형 순서열의 정규화 편집거리 유사도, 그리고 섹션 길이 비율 벡터의 L1 거리. "동일 골격" = 순서 유사도 1.0이고 L1 < 0.10 | rapidfuzz `Levenshtein.normalized_similarity` |

**[검증] datasketch 2.0의 함정.** 2.0.0(2026-07-05 배포)에서 기본 순열 방식이 `affine32`로 바뀌었다. 저장해 둔 해시값으로 MinHash를 복원할 때 `scheme`을 명시하지 않으면 `ValueError`가 난다. 이번 실행에서 실제로 재현했다. 아카이브 JSON에 `scheme`, `seed`, `num_perm`을 함께 저장해야 한다. 출처: https://raw.githubusercontent.com/ekzhu/datasketch/master/README.rst

```python
MinHash(num_perm=256, hashvalues=np.array(hv, dtype=np.uint32), scheme="affine32")
```

**[검증] 업계 참고값.**
- text-dedup의 MinHash 근접 중복 제거 기본 설정은 `ngram_size=5`, `num_perm=240`, `threshold=0.7`이다. 출처: https://raw.githubusercontent.com/ChenghaoMou/text-dedup/main/README.md
- Lee et al.(2021) 학습 데이터 중복 제거는 정확 부분문자열 기준으로 50토큰(=100바이트)을 썼다. 출처: https://raw.githubusercontent.com/google-research/deduplicate-text-datasets/master/README.md

이 기준들은 "거의 같은 문서"를 찾는 용도다. 플랫폼은 "변화가 거의 없음"을 문제 삼으므로 이 검사기는 더 엄격하게 잡았다. [추정]

### 3.3 시각 지표

**썸네일 거리 V1**

- d = min( pHash(a,b), pHash(a, 반전(b)), 엣지맵 pHash(a,b) ). 64비트 해밍거리다. 라이브러리는 imagehash 4.3.2.
- 반전과 엣지맵을 함께 보는 이유: 배경색과 글자색만 바꾸면 순수 pHash로는 56으로 "완전히 다름"이 나왔다. 엣지맵이나 반전 비교를 쓰면 6~8로 잡혔다. **[실험]**
- **[검증]** imagehash 해시 계열(aHash·pHash·dHash·wHash)은 밝기 구조만 보고, colorhash는 위치 정보 없이 색 분포만 본다. 출처: https://raw.githubusercontent.com/JohannesBuchner/imagehash/master/README.rst

**영상 간 소재 재사용률 V2**

1. ffmpeg로 프레임을 추출한다. 쇼츠는 `fps=1`, 롱폼은 `fps=0.5`, 모두 `scale=320:-1`.
2. 프레임마다 pHash를 구한다.
3. 정보량이 낮은 프레임(흑백 표준편차 < 8)을 뺀다. PDQ의 품질 필터와 같은 역할이다.
4. 직전 프레임과 거리 4 이하인 연속 프레임은 하나로 줄인다.
5. 프레임 해시를 **순서 없는 집합(bag-of-hashes)**으로 보고, 비교 영상의 어떤 프레임과 D≤8로 일치하는 질의 프레임의 비율을 계산한다.
6. 허용 상용구 구간(브랜드 범퍼)은 제외한다.

근거:
- **[검증]** Meta vPDQ 알고리즘(프레임 PDQ 해시의 순서 없는 집합 + 일치 비율, 예시 D=31, 품질 F=50, 비교 일치율 80%). 출처: https://raw.githubusercontent.com/facebook/ThreatExchange/main/vpdq/README.md
- **[검증]** PDQ 권장 임계값: 일치 거리 ≤31(256비트 기준), 품질 ≤49는 폐기. 출처: https://raw.githubusercontent.com/facebook/ThreatExchange/main/pdq/README.md, https://raw.githubusercontent.com/facebook/ThreatExchange/main/python-threatexchange/threatexchange/signal_type/pdq/signal.py
- 31/256 ≈ 12.1%를 64비트로 환산하면 약 7.75이므로 **D=8**을 쓴다. 이 환산은 [추정]이다.
- **[실험]** v1의 앞 6초를 재사용한 20초 영상 v2에서 재사용률이 0.30으로 측정됐다. 기대값 6/20=0.30과 일치한다.
- **[실험]** 계산 속도: numpy `np.bitwise_count`로 질의 1800 프레임 × 아카이브 93,600 프레임(롱폼 52편 분량)을 1.5초에 비교했다.

**영상 내부 지표 V3·V4·V5**

| ID | 지표 | 계산 |
|---|---|---|
| V3 | 고유 프레임 비율 | 1fps pHash를 D≤6으로 묶은 군집 수 ÷ 프레임 수 |
| V4 | 정지 비율·최장 정지 | `freezedetect=n=-60dB:d=2`. 로그의 `freeze_start` / `freeze_duration`을 파싱 |
| V5 | 컷 밀도 | `scdet=threshold=10`. 로그의 `lavfi.scd.time`을 셈 |

**[검증]** ffmpeg 필터 설명. 출처: https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/filters.texi
- freezedetect는 프레임 간 평균 절대차를 노이즈 기준과 비교한다. 기본값은 -60dB(0.001)와 2초다.
- scdet는 "좋은 값은 [8.0, 14.0]", 기본값은 10이다.
- silencedetect 기본값은 -60dB와 2초다.

**[실험]** 20초 중 8초 정지 영상에서 정지 1구간, 8.0초, 정지 비율 0.40, 고유 프레임 비율 0.65가 나왔다. 로그 형식(`freeze_start: 6`, `lavfi.scd.score: 29.429, lavfi.scd.time: 6`, `silence_start: 5`)은 ffmpeg 6.1.1에서 확인했다.

**주의 [추정].** 정지 이미지에 천천히 줌·패닝(Ken Burns 효과)을 걸면 freezedetect(-60dB)가 정지로 잡지 못한다. 그래서 V4만 보지 않고 V3(D≤6 군집)를 함께 본다. 반대로 운영자 얼굴이 나오는 토킹헤드 영상은 V3가 낮게 나와도 정상이다. 그래서 V3는 단독으로 차단하지 않고 A1(내레이션 비중)과 결합해서만 차단한다(C2).

### 3.4 내레이션·부가가치 지표

| ID | 지표 | 계산 |
|---|---|---|
| A1 | 내레이션 비중 | **음악을 섞기 전 내레이션 단독 트랙**에 `silencedetect=n=-35dB:d=0.6`을 적용하고 1 − 무음 합 ÷ 길이로 계산. 배경음악이 섞이면 무음이 사라져 과대평가되므로 단독 트랙이 필수다. [추정] |
| A2 | 발화 밀도 | 대본 글자 수 ÷ 내레이션 분. 참고용 |
| A3 | 음성 출처 | own / tts / clone. clone이면 당사자 동의 필수, 실존 인물이면 D1 공개 필수 |
| R1 | 제3자 소재 비중 | 소재 목록의 `third_party_*` 구간 합 ÷ 길이. 무허가 소재는 즉시 차단 |
| P1 | 부가가치 점수 0~100 | 0.30 × 독창 대본 비중(1 − max(S1, S4)) + 0.20 × 자체 데이터 요소(계산·표·실측 수치, 5분당 3개면 만점) + 0.20 × 자체 제작 소재 비중 + 0.15 × 컷 밀도 정규화 + 0.15 × A1. 가중치는 전부 [추정] |
| D1 | 합성 콘텐츠 공개 | 사실적 AI 소재(실존 인물, 실제 장소·사건, 음성 복제)가 있는데 `youtube.containsSyntheticMedia`가 true가 아니면 차단. TikTok·Meta 라벨 필드명은 [기억·미확인] |

### 3.5 핵심 계산 코드 (실행으로 검증한 API 기준)

```python
import re, subprocess, unicodedata, numpy as np, imagehash
from PIL import Image, ImageOps, ImageFilter
from datasketch import MinHash, MinHashLSH

def norm_ko(t, boiler=()):
    t = unicodedata.normalize("NFC", t)
    for b in boiler: t = t.replace(b, " ")
    t = re.sub(r"\d", "0", t)
    return re.sub(r"[^\w가-힣]", "", t)          # 공백·기호 제거

def shingles(t, n=5): return {t[i:i+n] for i in range(len(t)-n+1)}

def minhash(sh, num_perm=256):
    m = MinHash(num_perm=num_perm, scheme="affine32")
    for s in sh: m.update(s.encode("utf-8"))
    return m                                      # 저장: m.hashvalues.tolist() + scheme/seed

def thumb_dist(a_path, b_path):
    a, b = Image.open(a_path).convert("RGB"), Image.open(b_path).convert("RGB")
    edge = lambda im: imagehash.phash(ImageOps.grayscale(im).resize((256,144))
                                      .filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3)))
    pa = imagehash.phash(a)
    return min(pa - imagehash.phash(b), pa - imagehash.phash(ImageOps.invert(b)), edge(a) - edge(b))

def frame_hashes(video, fps=1.0, min_std=8, prune=4):
    # ffmpeg -i video -vf fps=1,scale=320:-1 dir/f_%05d.png 로 뽑은 뒤 pHash
    ...

def reuse_ratio(q: np.ndarray, c: np.ndarray, D=8, chunk=256):   # uint64 배열
    if len(q) == 0 or len(c) == 0: return 0.0
    hit = np.zeros(len(q), bool)
    for i in range(0, len(q), chunk):
        hit[i:i+chunk] = (np.bitwise_count(q[i:i+chunk, None] ^ c[None, :]) <= D).any(1)
    return float(hit.mean())

def freeze_stats(video):
    err = subprocess.run(["ffmpeg","-hide_banner","-i",video,"-vf","freezedetect=n=-60dB:d=2",
                          "-map","0:v:0","-f","null","-"], capture_output=True, text=True).stderr
    d = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", err)]
    return sum(d), max(d, default=0.0)
```

---

## 4. 비교 대상과 아카이브 인덱스

- **과거 발행본 아카이브.** 로컬 JSON 하나 `archive/index.json`(또는 항목별 JSONL)에 저장한다.
  - 텍스트는 원문 대신 MinHash 해시값과 문장 sha1 집합을 저장한다. 원문은 별도 경로로 둔다.
  - 시각 자료는 pHash 16진수 문자열을 저장한다.
  - 30분 롱폼(0.5fps, 연속 중복 정리 전)은 900해시 × 16자, 약 15KB다.
- **실행할 때마다 메모리에 MinHashLSH를 새로 만든다.** 1년 분량(쇼츠 728편 + 롱폼 52편)은 몇 초 안에 끝난다.
- **비교 범위**

| 검사 | 비교 범위 |
|---|---|
| S1·S2·V2 | 전체 아카이브 |
| M1·V1 | 최근 180일 |
| S5·T1 | 롱폼 최근 8~12편 |
| 배치 검사 | 같은 주 14편 쌍별 + 같은 주 롱폼 |

- **예외: 쇼츠와 그 쇼츠를 잘라낸 같은 주 롱폼 사이의 비교는 판정에서 뺀다.** 자기 롱폼을 재가공한 것이기 때문이다. 참고용 값으로만 기록한다. 쇼츠끼리는 엄격하게 본다.
- **인덱스 갱신은 발행 성공 후에만 한다.** 플랫폼 영상 ID를 받은 뒤 기록한다. 검사만 하고 발행하지 않은 항목은 넣지 않는다.

---

## 5. 소규모 보정 실험 결과 [실험]

합성 예시로 측정했다. 임계값의 근거가 아니라 값 범위를 보기 위한 것이다.

**대본 (A = 휴대폰 요금제 대본)**

| 쌍 | 문자 5-gram Jaccard | MinHash 추정(256) | TF-IDF 코사인 | 문장 중복 |
|---|---|---|---|---|
| A vs 다른 주제(같은 인트로·아웃트로) | 0.16 | 0.15 | 0.30 | 0.33 (상용구 때문) |
| A vs 문장 30%와 숫자 수정본 | 0.68 | 0.72 | 0.81 | 0.85 |
| A vs 문장 순서만 섞음 | 0.81 | 0.81 | 1.00 | 1.00 |
| A vs 같은 주제를 새로 씀 | 0.01 | 0.01 | 0.13 | 0.00 |

**제목 (문자 2-gram Jaccard)**

| 쌍 | 값 |
|---|---|
| "4인 가족"을 "1인 가구"로만 바꿈 | 0.75 |
| 같은 문형, 다른 주제 | 0.24 |
| 완전히 다른 제목 | 0.02 |

**썸네일 (64비트 해밍거리)**

| 비교 | 순수 pHash | 반전·엣지 포함 최솟값 |
|---|---|---|
| 재인코딩 사본 | 0 | 0~2 |
| 같은 템플릿, 글자만 바꿈 | 12 | 12 |
| 색만 바꿈 | 56 | 6~8 |
| 새 레이아웃 | 36 | 26~34 |

**보정 절차 [추정].** 4~8주 실운영 뒤 "정상적으로 서로 다른" 자기 에피소드 쌍의 S1·V2 분포를 구한다. 경고선을 그 분포의 95번째 백분위수(p95)로 다시 맞춘다. 차단선은 고정한다.

---

## 6. 업계의 비슷한 사전 검사 사례

| 사례 | 내용 | 상태 · 출처 |
|---|---|---|
| Meta PDQ / vPDQ / TMK+PDQF | 2019년 공개된 사진·영상 지각 해시. 대규모 중복·재업로드 탐지에 쓰인다. TMK는 1단계·2단계 점수 임계값으로 각각 0.7을 제안한다. HMA(Hasher-Matcher-Actioner)가 이 구현 위에 만들어졌다. | [검증] https://raw.githubusercontent.com/facebook/ThreatExchange/main/tmk/README.md, pdq/README.md |
| LLM 학습 데이터 중복 제거 | MinHash와 LSH로 근접 중복 문서를 걸러낸다(5-gram, 임계값 0.7). 정확 부분문자열(50토큰) 방식도 쓴다. | [검증] text-dedup, google-research README |
| YouTube Studio 업로드 "검사" | 게시 전 저작권(Content ID) 검사와 광고 적합성 자가 진단 | [기억·미확인] |
| TikTok Studio "Content Check Lite" | 게시 전 FYF 적격 여부 점검 | [기억·미확인] |
| Meta 전문가 대시보드 | 계정 상태(추천 적격 여부), 2025년 게시물 단위 배포 제한 사유 표시 | [기억·미확인] |
| 상용 콘텐츠 매칭 | Content ID, Audible Magic, Pex 등 | [기억·미확인] |

PyPI 현재 버전 [검증] (https://pypi.org/pypi/<패키지명>/json):

| 패키지 | 버전 | 비고 |
|---|---|---|
| imagehash | 4.3.2 | |
| datasketch | 2.0.0 | |
| scikit-learn | 1.9.1 | Python ≥3.11 |
| numpy | 2.5.3 | Python ≥3.12 |
| pillow | 12.3.0 | |
| rapidfuzz | 3.14.6 | |
| scenedetect | 0.7.1 | |
| pdqhash | 0.2.8 | |
| vpdq | 0.2.5 | |
| text-dedup | 0.4.1 | |
| videohash | 3.0.1 | 2022년 이후 갱신 없음, 비권장 |

---

## 7. 검사기 사양

### 7.1 동작 위치와 판정 처리

`/shorts`, `/longform`이 만든 결과물에 `/weekly-publish start`가 업로드 직전에 검사기를 실행한다.

| 판정 | 처리 |
|---|---|
| **BLOCK** | 해당 항목을 발행에서 빼고 예비 대기열에서 대체 항목을 넣는다. 대체 항목도 검사한다. |
| **WARN** | 발행은 하되 리포트에 기록한다. 같은 규칙이 **2주 연속** WARN이면 다음 주에는 BLOCK으로 올린다(무인 운영용 자동 승격). |
| **PASS** | 그대로 발행한다. |

채널 단위 규칙: 최근 30일 발행물 중 WARN 비율이 30%를 넘으면 `human_review_required=true`로 표시하고 그 주 발행을 보류한다. [추정]

### 7.2 입력 JSON 스키마 (CheckRequest)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "CheckRequest",
  "type": "object",
  "required": ["schema_version", "run_id", "mode", "archive_index_path", "items"],
  "properties": {
    "schema_version": {"const": "1.0"},
    "run_id": {"type": "string"},
    "mode": {"enum": ["single", "batch"]},
    "batch_id": {"type": "string", "description": "예: 2026-W41"},
    "archive_index_path": {"type": "string"},
    "config_path": {"type": "string", "description": "임계값 YAML. 없으면 기본값"},
    "boilerplate": {
      "type": "object",
      "properties": {
        "sentences": {"type": "array", "items": {"type": "string"}},
        "description_blocks": {"type": "array", "items": {"type": "string"}},
        "frame_hashes": {"type": "array", "items": {"type": "string"}, "description": "인트로·범퍼 프레임 pHash"}
      }
    },
    "banned_phrases": {"type": "array", "items": {"type": "string"}},
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["item_id", "kind", "script_path", "metadata"],
        "properties": {
          "item_id": {"type": "string"},
          "kind": {"enum": ["longform", "shorts", "card_news", "blog"]},
          "parent_longform_id": {"type": ["string", "null"]},
          "platforms": {"type": "array", "items": {"enum": ["youtube", "instagram", "facebook", "tiktok", "tistory"]}},
          "script_path": {"type": "string"},
          "sections": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["type", "start_s", "end_s"],
              "properties": {
                "type": {"enum": ["hook", "intro", "problem", "calc", "example", "tip", "cta", "outro", "other"]},
                "start_s": {"type": "number"}, "end_s": {"type": "number"}
              }
            }
          },
          "video_path": {"type": ["string", "null"]},
          "narration_stem_path": {"type": ["string", "null"], "description": "음악을 섞기 전 내레이션 단독 트랙"},
          "voice_type": {"enum": ["own", "tts", "clone"]},
          "voice_consent_ref": {"type": ["string", "null"]},
          "thumbnail_path": {"type": ["string", "null"]},
          "image_paths": {"type": "array", "items": {"type": "string"}, "description": "카드뉴스 장별 이미지"},
          "metadata": {
            "type": "object",
            "required": ["title"],
            "properties": {
              "title": {"type": "string"},
              "description": {"type": "string"},
              "tags": {"type": "array", "items": {"type": "string"}},
              "hashtags": {"type": "array", "items": {"type": "string"}}
            }
          },
          "sources": {
            "type": "array",
            "items": {"type": "object", "required": ["url", "text_path"],
                      "properties": {"url": {"type": "string"}, "text_path": {"type": "string"}}}
          },
          "assets": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["asset_id", "origin", "start_s", "end_s"],
              "properties": {
                "asset_id": {"type": "string"},
                "origin": {"enum": ["own_recorded", "own_made", "stock_licensed", "ai_generated",
                                    "third_party_licensed", "third_party_unlicensed"]},
                "start_s": {"type": "number"}, "end_s": {"type": "number"},
                "ai_realistic": {"type": "boolean"},
                "depicts_real_person_or_event": {"type": "boolean"},
                "license_ref": {"type": ["string", "null"]}
              }
            }
          },
          "publish_payload": {
            "type": "object",
            "properties": {
              "youtube": {"type": "object", "properties": {"containsSyntheticMedia": {"type": "boolean"}}},
              "tiktok": {"type": "object", "properties": {"aigc_label": {"type": "boolean"}}},
              "meta": {"type": "object", "properties": {"ai_label": {"type": "boolean"}}}
            }
          }
        }
      }
    }
  }
}
```

`tiktok.aigc_label`과 `meta.ai_label`은 내부 필드명이다. 실제 API 필드에 매핑하는 부분은 [기억·미확인]이다.

### 7.3 출력 JSON 스키마 (CheckReport)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "CheckReport",
  "type": "object",
  "required": ["schema_version", "run_id", "checked_at", "verdict", "items"],
  "properties": {
    "schema_version": {"const": "1.0"},
    "run_id": {"type": "string"},
    "checked_at": {"type": "string", "format": "date-time"},
    "tool_versions": {"type": "object", "additionalProperties": {"type": "string"}},
    "verdict": {"enum": ["PASS", "WARN", "BLOCK"]},
    "human_review_required": {"type": "boolean"},
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["item_id", "verdict", "metrics", "reasons"],
        "properties": {
          "item_id": {"type": "string"},
          "verdict": {"enum": ["PASS", "WARN", "BLOCK"]},
          "metrics": {
            "type": "object",
            "additionalProperties": {
              "type": "object",
              "required": ["value", "status"],
              "properties": {
                "value": {"type": ["number", "boolean", "null"]},
                "warn_at": {"type": ["number", "null"]},
                "block_at": {"type": ["number", "null"]},
                "status": {"enum": ["PASS", "WARN", "BLOCK", "SKIP", "INFO"]},
                "compared_with": {"type": ["string", "null"]},
                "detail": {"type": "object"}
              }
            }
          },
          "reasons": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["rule_id", "status", "message_ko"],
              "properties": {
                "rule_id": {"type": "string"},
                "status": {"enum": ["WARN", "BLOCK"]},
                "message_ko": {"type": "string"},
                "fix_hint_ko": {"type": "string"},
                "escalated_from_warn": {"type": "boolean"}
              }
            }
          },
          "required_disclosures": {
            "type": "object",
            "properties": {
              "youtube_containsSyntheticMedia": {"type": "boolean"},
              "tiktok_aigc_label": {"type": "boolean"},
              "meta_ai_label": {"type": "boolean"}
            }
          },
          "value_add_score": {"type": "number", "minimum": 0, "maximum": 100}
        }
      }
    },
    "batch": {
      "type": "object",
      "properties": {
        "verdict": {"enum": ["PASS", "WARN", "BLOCK"]},
        "pairwise_max": {"type": "object", "properties": {
          "script": {"type": "number"}, "visual": {"type": "number"},
          "title": {"type": "number"}, "thumb_min_dist": {"type": "integer"}}},
        "near_dup_clusters": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "title_skeleton_share": {"type": "number"},
        "thumb_template_share": {"type": "number"},
        "diversity_score": {"type": "number", "description": "1 - 쌍별 S1 평균"}
      }
    },
    "channel_rolling_30d": {
      "type": "object",
      "properties": {"items": {"type": "integer"}, "warn_ratio": {"type": "number"}, "block_count": {"type": "integer"}}
    }
  }
}
```

아카이브 인덱스 항목은 발행 성공 후 기록한다.

```json
{"item_id": "...", "kind": "shorts", "published_at": "...", "platform_ids": {"youtube": "..."},
 "title_norm": "...", "desc_body_norm": "...", "tags": [], "sentence_sha1": [],
 "script_minhash": {"num_perm": 256, "seed": 1, "scheme": "affine32", "ngram": 5, "hashvalues": []},
 "skeleton": ["hook", "problem", "calc", "cta"], "duration_profile": [0.1, 0.3, 0.5, 0.1],
 "thumb": {"phash": "hex", "phash_inv": "hex", "edge_phash": "hex"},
 "frames": {"fps": 1.0, "hashes": ["hex"]}, "metrics_snapshot": {}}
```

### 7.4 판정 규칙 표

L = 롱폼, S = 쇼츠. 근거: 실 = 5절 실험, 정 = 정책 문구(1절, 기억·미확인), 검 = 검증된 공개 기준, 추 = 추정.

| ID | 지표 | 비교 대상 | 통과 | 경고 | 차단 | 근거 |
|---|---|---|---|---|---|---|
| S1 | 본문 문자 5-gram Jaccard 최댓값 (상용구 제거, 숫자 정규화) | 아카이브 + 배치 | < 0.30 | 0.30~0.50 | ≥ 0.50 | 실·검(0.7보다 엄격)·정 |
| S2 | 문장 재사용률 | 아카이브 | < 0.10 | 0.10~0.25 | ≥ 0.25 | 추 |
| S3 | 영상 내부 동일 문장 비율 | 자기 자신 | < 0.10 | 0.10~0.25 | ≥ 0.25 | 정·추 |
| S4 | 출처 원문 8-gram 포함도 | 조사 출처 | < 0.15 | 0.15~0.35, 또는 80자 이상 일치 구간 ≥1 | ≥ 0.35 | 정(낭독 금지)·추 |
| S5 | 주제 TF-IDF 코사인 | 롱폼 최근 12주 | < 0.60 | ≥ 0.60 (새 각도 필요) | 없음 | 실 |
| T1 | 동일 골격 횟수 | 롱폼 최근 8편 | ≤ 5 | 6~8 | 단독 차단 없음 | 정(인트로·아웃트로 허용)·추 |
| M1 | 제목 문자 2-gram Jaccard | 최근 180일 + 배치 | < 0.45 | 0.45~0.65 | ≥ 0.65 | 실 |
| M2 | 제목 골격 공유 비율 | 배치 | ≤ 0.40 | > 0.40 | 없음 | 추 |
| M3 | 설명 본문 Jaccard (고정 블록 제거) | 최근 180일 + 배치 | < 0.50 | 0.50~0.80 | ≥ 0.80 | 추 |
| M4 | 태그·해시태그 | 배치, 규격 | 태그 Jaccard 평균 < 0.8 | 평균 ≥ 0.8 | 해시태그 > 60개, 태그 > 500자 | 기억·미확인 규격 |
| V1 | 썸네일 거리 (pHash·반전·엣지 최솟값) | 최근 180일 | ≥ 13 | 7~12, 또는 배치의 d≤12 쌍 비율 ≥ 80% | ≤ 6 (재사용) | 실 |
| V2a | 프레임 재사용률 (D=8, 단일 에피소드 대비) | 아카이브 | < 0.25 | 0.25~0.50 | ≥ 0.50 | 검(vPDQ)·추 |
| V2b | 프레임 재사용률 (아카이브 합집합 대비) | 아카이브 전체 | < 0.40 | 0.40~0.60 | ≥ 0.60 | 추 |
| V2c | 쇼츠 쌍별 재사용률 | 같은 주 쇼츠 | < 0.30 | 0.30~0.50 | ≥ 0.50 | 추 |
| V3 | 고유 프레임 비율 | 자기 자신 | L ≥ 0.35 / S ≥ 0.50 | 그 미만 | 단독 차단 없음(C2) | 실·추 |
| V4a | 정지 비율 (freezedetect -60dB, 2s) | 자기 자신 | L < 0.25 / S < 0.20 | L 0.25~0.50 / S 0.20~0.40 | L ≥ 0.50 / S ≥ 0.40 | 검(필터)·정(슬라이드쇼)·추 |
| V4b | 최장 정지 구간 | 자기 자신 | L < 15s / S < 4s | L 15~45s / S 4~8s | L ≥ 45s / S ≥ 8s | 추 |
| V5 | 컷 밀도 (scdet 10) | 자기 자신 | 참고 | L < 1회/분, S < 3회/30초 | 없음 | 검(필터)·추 |
| A1 | 내레이션 비중 (단독 트랙) | 자기 자신 | ≥ 0.60 | 0.40~0.60 | 단독 차단 없음(C2) | 정(해설·서사)·추 |
| A3 | 음성 출처 | 입력값 | own / tts | tts 사용은 INFO | clone인데 동의 기록 없음 | 정·추 |
| R1 | 제3자 소재 비중 | 소재 목록 | < 0.20 | 0.20~0.50 | ≥ 0.50, 또는 무허가 소재 1건 이상 | 정(reused) |
| P1 | 부가가치 점수 | 자기 자신 | ≥ 50 | < 50 | 단독 차단 없음 | 추 |
| D1 | 합성 콘텐츠 공개 | 소재 목록, 발행 값 | 공개 일치 | 없음 | 사실적 AI·음성 복제가 있는데 공개 값이 false | 검(API 필드)·정 |
| B0 | 금지 표현 | 설정 목록 | 0건 | 없음 | 1건 이상 | 운영자 규칙 |
| C1 | 템플릿 대량생산 결합 | 위 지표 | | | T1 동일 골격 **그리고** S1 ≥ 0.30 **그리고** (V1 ≤ 12 **또는** V2a ≥ 0.25) | 정 |
| C2 | 슬라이드쇼·저부가 결합 | 위 지표 | | | (V4a 차단선 이상 **또는** V3 < 0.20) **그리고** A1 < 0.40 | 정 |
| C3 | 배치 근접 중복 군집 | 같은 주 | 군집 없음 | 쌍별 경고(S1·V2c)가 4편 이상 | S1 ≥ 0.5 또는 V2c ≥ 0.5로 묶인 군집이 3편 이상이면, 군집 첫 편을 뺀 나머지 차단 | 정(대규모 복제) |
| E1 | 경고 자동 승격 | 직전 주 리포트 | | | 같은 규칙이 2주 연속 WARN | 추 |
| H1 | 채널 30일 경고 비율 | 최근 30일 | ≤ 0.30 | > 0.30이면 `human_review_required` | | 정(채널 단위 심사)·추 |

**종합 판정 순서**
1. 항목 판정: BLOCK이 하나라도 있으면 BLOCK.
2. 그렇지 않고 WARN이 3개 이상이거나 C1·C2 조건 중 하나가 경고 수준이면 WARN.
3. 그 밖에는 PASS.
4. 배치 판정: C3 차단이 있거나, 14편 중 BLOCK 항목을 대체할 예비분이 없으면 BLOCK.
5. 쇼츠 대 원본 롱폼(`parent_longform_id`) 비교는 판정에서 제외하고 INFO로만 남긴다.
6. 입력 파일이 없는 지표는 SKIP으로 둔다. 단, 롱폼과 쇼츠에서 `video_path`가 없으면 BLOCK이다. 검사를 생략한 채 발행하지 않기 위해서다.

---

## 8. 구현 전에 사람이 원문과 대조할 항목

1. YouTube 수익화 정책 페이지의 inauthentic·reused 원문 문구와 예시 목록, 2025-07-15 공지 문구 (support.google.com/youtube/answer/1311392)
2. YouTube 변형·합성 콘텐츠 공개 기준 (support.google.com/youtube/answer/14328491). Data API 필드 `status.containsSyntheticMedia`는 [검증]됨.
3. YouTube 제목·설명·태그 길이 제한, 해시태그 60개 규칙
4. Facebook 2025-07 독창성 강화 공지 원문과 URL, Instagram 2024-04 "30일 10회" 기준, Facebook 인스트림 광고의 정지·슬라이드쇼 제한 원문
5. TikTok FYF 적격성 기준의 "unoriginal / low-quality" 정의 원문, AIGC 라벨의 Content Posting API 필드명, Creator Rewards의 1분 기준, Content Check Lite 존재 여부
6. 모든 임계값([추정] 표시)은 실운영 4~8주 데이터로 p95를 기준으로 다시 맞출 것

## 출처

**이번 실행에서 원문을 직접 확인한 것 [검증]**
- https://raw.githubusercontent.com/googleapis/google-api-go-client/main/youtube/v3/youtube-api.json (YouTube Data API v3 디스커버리, revision 20261001)
- https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/doc/filters.texi (freezedetect, scdet, silencedetect, mpdecimate, blackdetect)
- https://raw.githubusercontent.com/facebook/ThreatExchange/main/pdq/README.md
- https://raw.githubusercontent.com/facebook/ThreatExchange/main/vpdq/README.md
- https://raw.githubusercontent.com/facebook/ThreatExchange/main/tmk/README.md
- https://raw.githubusercontent.com/facebook/ThreatExchange/main/python-threatexchange/threatexchange/signal_type/pdq/signal.py
- https://raw.githubusercontent.com/JohannesBuchner/imagehash/master/README.rst
- https://raw.githubusercontent.com/ekzhu/datasketch/master/README.rst
- https://raw.githubusercontent.com/ChenghaoMou/text-dedup/main/README.md
- https://raw.githubusercontent.com/google-research/deduplicate-text-datasets/master/README.md
- https://pypi.org/pypi/imagehash/json , https://pypi.org/pypi/datasketch/json , https://pypi.org/pypi/scikit-learn/json , https://pypi.org/pypi/numpy/json , https://pypi.org/pypi/pillow/json , https://pypi.org/pypi/rapidfuzz/json , https://pypi.org/pypi/scenedetect/json , https://pypi.org/pypi/pdqhash/json , https://pypi.org/pypi/vpdq/json , https://pypi.org/pypi/text-dedup/json , https://pypi.org/pypi/videohash/json

**확인할 공식 문서 (이번 실행에서 열람 실패, 미확인)**
- https://support.google.com/youtube/answer/1311392
- https://support.google.com/youtube/answer/14328491
- https://support.google.com/youtube/answer/2801973
- https://support.google.com/youtube/answer/6390658
- https://www.tiktok.com/community-guidelines/en/fyf-standards
- https://help.instagram.com/313829416281232
- Meta 2025-07 독창성 강화 공지 (creators.facebook.com/blog 또는 about.fb.com/news, 정확한 URL 미확인)

**실험 스크립트 (스크래치패드)**
- /tmp/claude-0/-home-user-sjcu-python/51908c08-0703-53d5-87fc-b6b14754da05/scratchpad/exp_text.py
- /tmp/claude-0/-home-user-sjcu-python/51908c08-0703-53d5-87fc-b6b14754da05/scratchpad/exp_media.py