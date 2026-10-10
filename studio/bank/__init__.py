"""문제은행 — 공인중개사 시험 대비 문항(OX·5지선다·빈칸·순서·계산)의 저장·검증·앱 팩.

  models.py      문항 규격(BankItem·Basis) — content/bank/<과목코드>.jsonl
  store.py       잠금·읽기·쓰기
  dedupe.py      지문 3-gram 자카드 중복 탐지
  exam.py        channel.yaml exam·bank 설정(기본값 포함)
  export.py      앱 팩(pack_schema.json 규격) 만들기 / upload.py 스토리지 업로드
  watch.py       인용 법령 개정 감지 → retired
  cli.py         python -m studio bank …
법령 원문 대조는 studio.verify 가 맡는다.
"""
