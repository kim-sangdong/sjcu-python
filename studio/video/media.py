"""ffmpeg/ffprobe 호출과 WAV 입출력 도우미.

영상 엔진의 모든 오디오는 48kHz·모노·16bit PCM WAV 로 맞춘 뒤 합친다.
영상 세그먼트는 X264_ARGS 하나로만 인코딩해야 concat demuxer(-c copy)로 이어 붙일 수 있다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

FFMPEG = os.environ.get("STUDIO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("STUDIO_FFPROBE", "ffprobe")

SAMPLE_RATE = 48000
AUDIO_BITRATE = "128k"


class MediaError(RuntimeError):
    pass


def require_tools() -> None:
    for tool in (FFMPEG, FFPROBE):
        if not shutil.which(tool):
            raise MediaError(f"{tool} 를 찾지 못했습니다. ffmpeg 6.x 를 설치하세요 (Ubuntu: sudo apt-get install -y ffmpeg).")


def run(args: list[str], *, input: bytes | None = None, what: str = "ffmpeg") -> subprocess.CompletedProcess:
    """명령 실행. 실패하면 stderr 끝부분을 담아 MediaError."""
    res = subprocess.run(args, input=input, capture_output=True)
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()[-12:]
        raise MediaError(f"{what} 실패 (코드 {res.returncode}):\n" + "\n".join(tail))
    return res


def ffmpeg(*args: str, what: str = "ffmpeg") -> subprocess.CompletedProcess:
    return run([FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args], what=what)


def probe(path: Path) -> dict:
    res = run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        what="ffprobe",
    )
    return json.loads(res.stdout or b"{}")


def duration(path: Path) -> float:
    info = probe(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        raise MediaError(f"길이를 읽지 못했습니다: {path}")
    return float(d)


def x264_args(fps: int, threads: int = 2) -> list[str]:
    """모든 영상 세그먼트에 똑같이 쓰는 인코딩 인자(이어 붙이기 위해 반드시 같아야 한다)."""
    return [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "4.1",
        "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0",
        "-r", str(fps), "-video_track_timescale", str(fps * 512),
        "-threads", str(threads), "-an",
    ]


# ---------------------------------------------------------------- WAV

def read_wav(path: Path) -> np.ndarray:
    """48kHz 모노 16bit WAV → int16 배열."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != SAMPLE_RATE:
            raise MediaError(f"{path}: 48kHz 모노 16bit WAV 가 아닙니다. to_wav() 로 먼저 변환하세요.")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()


def write_wav(path: Path, samples: np.ndarray) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    tmp.replace(path)
    return path


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def to_wav(src: Path, dst: Path, *, loudnorm_i: float | None = None, highpass: bool = False) -> float:
    """아무 오디오 → 48kHz 모노 16bit WAV. loudnorm_i 를 주면 그 크기로 맞춘다(사람 녹음용)."""
    filters = []
    if highpass:
        filters.append("highpass=f=70")
    if loudnorm_i is not None:
        filters.append(f"loudnorm=I={loudnorm_i}:TP=-2:LRA=11")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part.wav")
    args = ["-i", str(src)]
    if filters:
        args += ["-af", ",".join(filters)]
    args += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp)]
    ffmpeg(*args, what=f"오디오 변환({Path(src).name})")
    tmp.replace(dst)
    return wav_seconds(dst)


def trim_silence(samples: np.ndarray, threshold_db: float = -45.0, pad: float = 0.06) -> np.ndarray:
    """앞뒤 무음 잘라내기(문장 사이 쉼은 타임라인이 일정하게 넣는다)."""
    if samples.size == 0:
        return samples
    thr = 32768.0 * (10 ** (threshold_db / 20))
    loud = np.flatnonzero(np.abs(samples.astype(np.int32)) > thr)
    if loud.size == 0:
        return samples
    p = int(pad * SAMPLE_RATE)
    return samples[max(0, loud[0] - p): min(samples.size, loud[-1] + p)]


def tone(freq: float, seconds: float, *, gain_db: float = -20.0, decay: float = 18.0) -> np.ndarray:
    """짧은 효과음(카운트다운 틱 등) 합성: 감쇠하는 사인파."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    env = np.exp(-decay * t) * np.minimum(1.0, t / 0.004)
    wave_ = np.sin(2 * np.pi * freq * t) * env
    return (wave_ * 32767 * (10 ** (gain_db / 20))).astype(np.int16)
