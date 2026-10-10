"""독창성(반복·대량생산 판정 회피) 검사기.

  python -m studio check originality --week 2026-W42 --stage script|render [--only ID]
  python -m studio archive add --week 2026-W42 [--only ID]

두 단계로 본다.
  script  렌더 전, 원고(JSON) 텍스트만: 대본·문장·고정 문구·구조·형식 순환·제목·설명·태그·사람 개입·AI 고지
  render  렌더 후, manifest.json 의 파일까지: 위 항목 + 썸네일 pHash·프레임 재사용·정지 비율

비교 대상은 archive/index.json 의 과거 발행 지문과 같은 주 묶음(같은 종류끼리, 발행 순서상 앞선 것)이다.
결과: content/weeks/<week>/originality.json (models.OriginalityReport) + originality.md
임계값: studio/originality/thresholds.py 기본값 위에 channel.yaml 의 originality 딕셔너리로 덮어쓴다.
"""

from studio.originality.checker import run_check  # noqa: F401
