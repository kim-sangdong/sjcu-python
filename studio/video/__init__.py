"""영상 엔진: TTS → 타임라인 → 장면 렌더 → 합성(mp4) → 썸네일.

  python -m studio render video --week 2026-W42 [--only ID ...] [--kind longform|short] [--tts silent]

모듈:
  tts.py        문장 단위 TTS(google / edge / silent) + 캐시
  design.py     색·글꼴·화면 규격·한국어 줄바꿈·그리기 도우미
  scenes/       장면 렌더러 레지스트리(@scene("이름"))와 kind 별 구현
  subtitles.py  자막 나누기·SRT·화면 자막
  compose.py    타임라인·세그먼트 인코딩·오디오·최종 mp4
  thumbnail.py  롱폼 썸네일(1280x720), 쇼츠 커버(1080x1920)
  media.py      ffmpeg/ffprobe·WAV 도우미
"""
