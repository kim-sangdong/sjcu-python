"""분석·매출 귀속·광고 판단 데이터.

  python -m studio analytics collect --week W [--sources youtube,instagram,facebook,tiktok,funnel] [--fake] [--dry-run]
      → reports/<week>/metrics.json  (원천별 원자료 + 수집 시각, 콘텐츠 ID 기준 정규화)
  python -m studio analytics report  --week W
      → reports/<week>/channel-revenue.md, channel-revenue.csv
  python -m studio analytics ad-data --week W
      → reports/<week>/ad-decision-data.json

구성: common(기간·설정·대기열 매핑·HTTP) · collect/*(수집기) · attribution(클릭→설치→결제 연결)
      · report(주간 보고) · addecision(광고 판단 규칙) · cli.
"""
