"""문장 단위 TTS.

공급자는 모두 `synthesize(text, out_path) -> 길이(초)` 를 따르고 48kHz 모노 16bit WAV 를 쓴다.
  google  Google Cloud TTS(기본 ko-KR-Chirp3-HD-Aoede). 자격증명: GOOGLE_APPLICATION_CREDENTIALS(파일 경로)
          또는 GOOGLE_TTS_CREDENTIALS_JSON(JSON 내용, CI용 → 임시 파일로 저장해 사용)
  edge    cfg.tts.fallback == "edge" 일 때의 대체 공급자(또는 명시적으로 강제했을 때). 약관상 상업 이용이 불분명 → 경고
  silent  네트워크 없이 글자 수로 길이를 추정한 무음. 테스트·리허설용. STUDIO_TTS=silent 또는 --tts silent

캐시: out/cache/tts/<sha256(공급자, 목소리, 속도, 문장)>.wav — 같은 문장은 다시 합성하지 않는다.
네트워크 실패는 지수 백오프로 최대 3회 시도한다.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Protocol, TypeVar

import numpy as np

from studio import paths
from studio.config import Config, MissingSecret
from studio.video import media

log = logging.getLogger("studio.video.tts")

# ---------------------------------------------------------------- 상수(조사 문서 기준, 바뀌면 여기만 고친다)

GOOGLE_LANGUAGE = "ko-KR"
# docs/research/facts-google.md §8: Cloud TTS v1 AudioConfig.speakingRate 범위 [0.25, 2.0], audioEncoding LINEAR16(WAV 헤더 포함)
GOOGLE_RATE_RANGE = (0.25, 2.0)
GOOGLE_AUDIO_ENCODING = "LINEAR16"
# Chirp 3 HD 가 speakingRate 를 받는지는 [미확인]. 보내 보고 INVALID_ARGUMENT 가 나면 빼고 다시 요청한다(경고 1회).
GOOGLE_RATE_ERROR_HINTS = ("speaking_rate", "speakingrate", "speaking rate", "pace", "rate")
GOOGLE_PARALLEL = 4               # 동시 요청 수(분당 요청 한도는 [미확인] — 429 가 나면 줄인다)
REQUEST_MAX_BYTES = 4800          # 요청당 입력 한도 5,000바이트 [미확인] — 문장 단위라 보통 훨씬 짧다

EDGE_WARNING = (
    "[경고] edge-tts 는 비공식 클라이언트이고 상업 이용 약관이 불분명합니다(docs/research/facts-google.md §8). "
    "발행물에는 쓰지 말고 미리듣기·리허설에만 쓰세요."
)

RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 1.0

# 무음 공급자의 길이 추정: studio.planning 과 같은 기준(공백 제외 글자/초, 속도 1.0 기준)
SILENT_CHARS_PER_SECOND = 5.5
SILENT_PAUSE = {",": 0.18, "·": 0.1, ".": 0.12, "?": 0.15, "!": 0.12, "…": 0.3}
SILENT_MIN_SECONDS = 0.8


class TTSError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class TTSProvider(Protocol):
    name: str
    voice: str
    rate: float

    def synthesize(self, text: str, out_path: Path) -> float: ...


T = TypeVar("T")


def with_retry(fn: Callable[[], T], *, what: str, attempts: int = RETRY_ATTEMPTS,
               base: float = RETRY_BASE_SECONDS, sleep: Callable[[float], None] = time.sleep) -> T:
    """지수 백오프(1s, 2s, …) 재시도. retryable=False 인 TTSError·MissingSecret 은 바로 올린다."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except MissingSecret:
            raise
        except TTSError as e:
            if not e.retryable:
                raise
            last = e
        except Exception as e:  # 네트워크·서버 오류
            last = e
        if i < attempts - 1:
            wait = base * (2 ** i)
            log.warning("%s 실패(%d/%d): %s — %.0f초 뒤 다시 시도", what, i + 1, attempts, _short(last), wait)
            sleep(wait)
    raise TTSError(f"{what}: {attempts}회 시도 모두 실패 — {_short(last)}")


def _short(e: Exception | None) -> str:
    s = f"{type(e).__name__}: {e}" if e else ""
    return s if len(s) < 300 else s[:300] + "…"


def clean_text(text: str) -> str:
    """화면용 강조 표시(**)·여러 칸 공백을 지운 낭독용 문장."""
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def _normalize_to_wav(raw: Path, out_path: Path, *, trim: bool) -> float:
    """임의 오디오 → 48kHz 모노 WAV, 앞뒤 무음 정리."""
    tmp = out_path.with_name(out_path.stem + ".norm.wav")
    media.to_wav(raw, tmp)
    if trim:
        media.write_wav(out_path, media.trim_silence(media.read_wav(tmp)))
        tmp.unlink(missing_ok=True)
    else:
        tmp.replace(out_path)
    return media.wav_seconds(out_path)


# ---------------------------------------------------------------- silent

class SilentTTS:
    """네트워크 없이 글자 수로 길이를 추정한 무음 WAV."""

    name = "silent"

    def __init__(self, voice: str = "-", rate: float = 1.0):
        self.voice = voice
        self.rate = rate

    @staticmethod
    def estimate(text: str, rate: float = 1.0) -> float:
        chars = len(re.sub(r"\s", "", text))
        pauses = sum(SILENT_PAUSE.get(ch, 0.0) for ch in text)
        return max(SILENT_MIN_SECONDS, chars / SILENT_CHARS_PER_SECOND / max(rate, 0.25) + pauses)

    def synthesize(self, text: str, out_path: Path) -> float:
        secs = self.estimate(clean_text(text), self.rate)
        media.write_wav(out_path, np.zeros(int(round(secs * media.SAMPLE_RATE)), dtype=np.int16))
        return media.wav_seconds(out_path)


# ---------------------------------------------------------------- google

_TEMP_CREDENTIALS: Path | None = None


def google_credentials_file() -> Path:
    """서비스계정 JSON 경로. GOOGLE_APPLICATION_CREDENTIALS 가 없으면 GOOGLE_TTS_CREDENTIALS_JSON 내용을 임시 파일로."""
    global _TEMP_CREDENTIALS
    from studio.config import load_env

    load_env()
    p = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if p:
        if not Path(p).exists():
            raise MissingSecret(f"GOOGLE_APPLICATION_CREDENTIALS 경로에 파일이 없습니다: {p}")
        return Path(p)
    raw = os.environ.get("GOOGLE_TTS_CREDENTIALS_JSON")
    if not raw:
        raise MissingSecret(
            "Google TTS 자격증명이 없습니다. 서비스계정 JSON 파일 경로를 GOOGLE_APPLICATION_CREDENTIALS 에, "
            "또는 JSON 내용을 GOOGLE_TTS_CREDENTIALS_JSON 에 넣어 주세요. 리허설은 --tts silent 로 할 수 있습니다."
        )
    if _TEMP_CREDENTIALS and _TEMP_CREDENTIALS.exists():
        return _TEMP_CREDENTIALS
    try:
        json.loads(raw)
    except json.JSONDecodeError as e:
        raise MissingSecret(f"GOOGLE_TTS_CREDENTIALS_JSON 이 올바른 JSON 이 아닙니다({e.msg}). 서비스계정 키 파일 내용을 그대로 넣어 주세요.")
    fd, name = tempfile.mkstemp(prefix="gtts-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(raw)
    os.chmod(name, 0o600)
    _TEMP_CREDENTIALS = Path(name)
    atexit.register(lambda: _TEMP_CREDENTIALS and _TEMP_CREDENTIALS.unlink(missing_ok=True))
    return _TEMP_CREDENTIALS


class GoogleTTS:
    name = "google"

    def __init__(self, voice: str, rate: float = 1.0, language: str = GOOGLE_LANGUAGE):
        lo, hi = GOOGLE_RATE_RANGE
        self.voice = voice
        self.rate = min(hi, max(lo, float(rate or 1.0)))
        self.language = language
        self._client = None
        self._lock = threading.Lock()
        self._send_rate = abs(self.rate - 1.0) > 1e-3
        self._warned = False

    def client(self):
        with self._lock:
            if self._client is None:
                from google.cloud import texttospeech

                cred = google_credentials_file()
                self._client = texttospeech.TextToSpeechClient.from_service_account_file(str(cred))
        return self._client

    def _request(self, text: str) -> bytes:
        from google.api_core import exceptions as gexc
        from google.cloud import texttospeech as tts

        if len(text.encode("utf-8")) > REQUEST_MAX_BYTES:
            raise TTSError(f"문장이 너무 깁니다({len(text.encode('utf-8'))}바이트). 원고에서 문장을 나눠 주세요: {text[:40]}…", retryable=False)
        audio = tts.AudioConfig(audio_encoding=getattr(tts.AudioEncoding, GOOGLE_AUDIO_ENCODING))
        if self._send_rate:
            audio.speaking_rate = self.rate
        req = dict(
            input=tts.SynthesisInput(text=text),
            voice=tts.VoiceSelectionParams(language_code=self.language, name=self.voice),
            audio_config=audio,
        )
        try:
            return self.client().synthesize_speech(**req).audio_content
        except gexc.InvalidArgument as e:
            msg = str(e).lower()
            if self._send_rate and any(h in msg for h in GOOGLE_RATE_ERROR_HINTS):
                self._send_rate = False
                if not self._warned:
                    self._warned = True
                    log.warning("[경고] 목소리 %s 가 speaking_rate 를 지원하지 않아 기본 속도로 합성합니다(설정값 %.2f 무시).", self.voice, self.rate)
                return self._request(text)
            raise TTSError(f"Google TTS 요청 오류(목소리 {self.voice}): {e.message if hasattr(e, 'message') else e}", retryable=False)
        except (gexc.PermissionDenied, gexc.Unauthenticated) as e:
            raise TTSError(
                f"Google TTS 권한 오류: {e}. 서비스계정에 Cloud Text-to-Speech API 사용 권한이 있는지, "
                "프로젝트에서 API 가 켜져 있는지 확인하세요.", retryable=False)
        except gexc.NotFound as e:
            raise TTSError(f"Google TTS: 목소리 {self.voice} 를 찾지 못했습니다({e}). channel.yaml tts.voice 를 확인하세요.", retryable=False)

    def synthesize(self, text: str, out_path: Path) -> float:
        text = clean_text(text)
        content = self._request(text)
        raw = out_path.with_name(out_path.stem + ".raw.wav")
        raw.write_bytes(content)
        try:
            return _normalize_to_wav(raw, out_path, trim=True)
        finally:
            raw.unlink(missing_ok=True)


# ---------------------------------------------------------------- edge

class EdgeTTS:
    name = "edge"

    def __init__(self, voice: str, rate: float = 1.0):
        self.voice = voice
        self.rate = float(rate or 1.0)

    def synthesize(self, text: str, out_path: Path) -> float:
        import edge_tts

        text = clean_text(text)
        pct = int(round((self.rate - 1.0) * 100))
        mp3 = out_path.with_name(out_path.stem + ".edge.mp3")

        async def go() -> None:
            await edge_tts.Communicate(text, self.voice, rate=f"{pct:+d}%").save(str(mp3))

        asyncio.run(go())
        try:
            return _normalize_to_wav(mp3, out_path, trim=True)
        finally:
            mp3.unlink(missing_ok=True)


# ---------------------------------------------------------------- 캐시

def cache_dir() -> Path:
    return paths.OUT / "cache" / "tts"


class CachedTTS:
    """공급자를 감싸 캐시·재시도를 붙인다. clip(text) 는 캐시 파일 경로와 길이를 돌려준다."""

    def __init__(self, provider: TTSProvider, directory: Path | None = None):
        self.provider = provider
        self.dir = directory or cache_dir()

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def voice(self) -> str:
        return self.provider.voice

    @property
    def rate(self) -> float:
        return self.provider.rate

    def key(self, text: str) -> str:
        p = self.provider
        return hashlib.sha256(f"{p.name}\x1f{p.voice}\x1f{p.rate:.3f}\x1f{clean_text(text)}".encode("utf-8")).hexdigest()

    def clip(self, text: str) -> tuple[Path, float]:
        path = self.dir / f"{self.key(text)}.wav"
        if path.exists() and path.stat().st_size > 44:
            return path, media.wav_seconds(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.tmp.wav")
        try:
            with_retry(lambda: self.provider.synthesize(text, tmp), what=f"TTS({self.provider.name}) '{clean_text(text)[:20]}…'")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return path, media.wav_seconds(path)

    def clips(self, texts: list[str], parallel: int | None = None) -> list[tuple[Path, float]]:
        """여러 문장(같은 문장은 한 번만 합성). 네트워크 공급자는 몇 개씩 동시에 요청한다."""
        uniq = list(dict.fromkeys(clean_text(t) for t in texts))
        n = parallel or (GOOGLE_PARALLEL if self.provider.name != "silent" else 1)
        if n <= 1 or len(uniq) <= 1:
            done = {t: self.clip(t) for t in uniq}
        else:
            with ThreadPoolExecutor(max_workers=n) as ex:
                done = dict(zip(uniq, ex.map(self.clip, uniq)))
        return [done[clean_text(t)] for t in texts]

    def synthesize(self, text: str, out_path: Path) -> float:
        src, secs = self.clip(text)
        if Path(out_path) != src:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out_path)
        return secs


# ---------------------------------------------------------------- 선택

def _make(name: str, cfg: Config) -> TTSProvider:
    t = cfg.tts
    if name == "silent":
        return SilentTTS(voice=t.voice, rate=t.speaking_rate)
    if name == "google":
        return GoogleTTS(voice=t.voice, rate=t.speaking_rate)
    if name == "edge":
        print(EDGE_WARNING)
        return EdgeTTS(voice=t.fallback_voice, rate=t.speaking_rate)
    raise TTSError(f"TTS 공급자 '{name}' 는 아직 지원하지 않습니다(google | edge | silent). channel.yaml tts.provider 를 확인하세요.", retryable=False)


def resolve(cfg: Config, override: str | None = None, *, dry_run: bool = False) -> CachedTTS:
    """사용할 공급자. 우선순위: override(--tts) > STUDIO_TTS > cfg.tts.provider. dry_run 이면 silent."""
    name = (override or os.environ.get("STUDIO_TTS") or cfg.tts.provider or "google").strip().lower()
    if dry_run and name != "silent":
        print(f"[dry-run] TTS({name}, 목소리 {cfg.tts.voice}) 요청을 보내지 않고 무음으로 대신합니다.")
        name = "silent"
    return CachedTTS(_make(name, cfg))


def fallback(cfg: Config, current: CachedTTS) -> CachedTTS | None:
    """주 공급자가 실패했을 때 쓸 대체 공급자(cfg.tts.fallback). edge 는 설정했을 때만."""
    fb = (cfg.tts.fallback or "none").lower()
    if fb in ("none", "", current.name):
        return None
    return CachedTTS(_make(fb, cfg))
