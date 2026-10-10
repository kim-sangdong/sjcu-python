"""영상 엔진 단위 테스트: 줄바꿈, 장면 레지스트리·레이아웃, 퀴즈 단계, 자막, TTS(모킹), 썸네일, 오디오 배치."""

from __future__ import annotations

import io
import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from studio import paths
from studio.config import Config, MissingSecret, load_config
from studio.models import Scene, Thumbnail, VideoScript
from studio.video import design as D

REPO = Path(__file__).resolve().parents[1]
HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name, sub in {"ROOT": "", "CONTENT": "content", "OUT": "out", "ASSETS": "assets"}.items():
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(paths, "FONTS", REPO / "assets" / "fonts")
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_TTS", raising=False)
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    load_config.cache_clear()
    yield tmp_path
    load_config.cache_clear()


@pytest.fixture(scope="module")
def cfg() -> Config:
    import yaml

    return Config.model_validate(yaml.safe_load((REPO / "channel.yaml").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def fonts(cfg):
    return D.Fonts(cfg.brand)


# ---------------------------------------------------------------- 줄바꿈

def test_wrap_keeps_eojeol_and_width(fonts):
    font = fonts.get("regular", 56)
    text = "되와 돼는 소리가 거의 같아서 귀로는 구분하기 어렵습니다"
    spans = D.wrap_spans(text, font, 400)
    lines = [text[s:e] for s, e in spans]
    assert len(lines) > 1
    for ln in lines:
        assert font.getlength(ln) <= 400
    # 어절 중간에서 끊지 않는다
    assert " ".join(lines) == text


def test_wrap_breaks_long_word_by_chars(fonts):
    font = fonts.get("regular", 56)
    word = "가나다라마바사아자차카타파하" * 2
    spans = D.wrap_spans(word, font, 300)
    assert len(spans) > 1
    assert "".join(word[s:e] for s, e in spans) == word
    assert all(font.getlength(word[s:e]) <= 300 for s, e in spans)


def test_balanced_wrap_never_breaks_words(fonts):
    font = fonts.get("bold", 62)
    text = "자연스러운지 판단"
    spans = D.balanced_spans(text, font, 420)
    assert [text[s:e] for s, e in spans] == ["자연스러운지", "판단"]


def test_text_block_shrinks_then_ellipsis(fonts):
    long = "아주 긴 문장입니다. " * 30
    tb = D.text_block(fonts, long, size=60, min_size=48, color=(0, 0, 0), max_w=600, max_h=200)
    assert tb.size == 48 and tb.overflow and tb.lines[-1].endswith("…")
    ok = D.text_block(fonts, "짧은 글", size=60, min_size=48, color=(0, 0, 0), max_w=600, max_h=200)
    assert ok.size == 60 and not ok.overflow


def test_emphasis_parsing():
    plain, mask = D.parse_emphasis("잘 **되면** 좋겠다")
    assert plain == "잘 되면 좋겠다"
    assert [plain[i] for i, m in enumerate(mask) if m] == ["되", "면"]


# ---------------------------------------------------------------- 장면

SAMPLES = {
    "title": dict(narration=["a", "b"], params={"title": "헷갈리는 맞춤법 한 번에 정리", "subtitle": "원리로 구분하기"}),
    "chapter": dict(narration=["a"], params={"title": "원리로 구분하기"}),
    "text": dict(narration=["a", "b", "c"], params={"heading": "왜 헷갈릴까요?", "body": "소리가 같습니다. 귀로는 구분이 안 됩니다. **'되어'로 바꿔 보기**가 확실합니다."}),
    "bullets": dict(narration=["a", "b", "c", "d"], params={"heading": "오늘 다룰 내용", "items": ["되와 돼 구분", "안과 않 구분", "문장으로 연습"]}),
    "quiz": dict(narration=["a", "b", "정답은 2번", "d"], params={"question": "다음 중 바른 것은?", "choices": ["안 되요", "안 돼요", "안 됬어요", "안 되어요"], "answer": 1, "explanation": "'돼'는 '되어'의 준말입니다.", "basis": "표준국어대사전"}, hold_seconds=5),
    "compare": dict(narration=["a", "b", "c"], params={"left_title": "되", "right_title": "돼", "rows": [["되고, 되니", "돼서, 돼요"], ["바꿀 수 없음", "바꿀 수 있음"]]}),
    "sequence": dict(narration=["a", "정답 순서", "c", "d", "e"], params={"items": ["자리 찾기", "넣어 읽기", "판단하기"]}),
    "timeline": dict(narration=["a", "b", "c"], params={"events": [{"label": "1933년", "text": "통일안"}, {"label": "1988년", "text": "현행 규정"}, {"label": "지금", "text": "사전 제공"}]}),
    "flow": dict(narration=["a", "b", "c"], params={"steps": ["읽기", "넣어 보기", "고르기"]}),
    "bars": dict(narration=["a", "b", "c"], params={"title": "비율", "items": [{"label": "가", "value": 62}, {"label": "나", "value": 48}, {"label": "다", "value": 35}], "unit": "%"}),
    "case": dict(narration=["a", "b"], params={"title": "문자 한 통", "body": "첫 문단.\n둘째 문단.", "reconstructed": True}),
    "quote": dict(narration=["a", "b"], params={"text": "말은 소리로, 글은 약속으로.", "source": "어느 교사"}),
    "cta": dict(narration=["a", "b"], params={"headline": "하루 한 문제", "body": "앱에서 모아 보세요.", "button": "설명란 링크"}),
    "outro": dict(narration=["a"], params={}),
}
REQUIRED_KINDS = {"title", "chapter", "text", "bullets", "quiz", "compare", "sequence", "timeline", "flow", "bars", "case",
                  "quote", "image", "voice", "cta", "outro"}


def _ctx(cfg, fonts, size, root: Path = Path("."), asset_id="2026W42-S01"):
    from studio.video.scenes import SceneContext

    g = D.Geometry.make(size)
    return SceneContext(cfg=cfg, geo=g, pal=D.Palette.from_brand(cfg.brand), fonts=fonts, asset_id=asset_id, fmt="short",
                        chapter_title="오늘의 문제", root=root)


def test_registry_has_all_kinds_and_rejects_unknown():
    from studio.video.scenes import SceneError, available, get_renderer

    assert REQUIRED_KINDS <= set(available())
    with pytest.raises(SceneError) as e:
        get_renderer("nope")
    assert "사용 가능" in str(e.value) and "quiz" in str(e.value)


def test_missing_params_is_clear_error(cfg, fonts):
    from studio.video.scenes import SceneError, get_renderer

    with pytest.raises(SceneError, match="choices"):
        get_renderer("quiz")(Scene(id="q", kind="quiz", params={"question": "?", "explanation": "x"}), _ctx(cfg, fonts, (1080, 1920)))
    with pytest.raises(SceneError, match="answer"):
        get_renderer("quiz")(Scene(id="q", kind="quiz", params={"question": "?", "choices": ["a", "b"], "answer": 5, "explanation": "x"}),
                             _ctx(cfg, fonts, (1080, 1920)))


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920)], ids=["landscape", "portrait"])
@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_scene_layouts_all_variants(cfg, fonts, kind, size):
    """모든 kind × 변형 3벌 × 가로/세로: 그려지고, 요소가 화면 안에 있고, 자막 영역과 겹치지 않는다."""
    from studio.video.scenes import get_renderer

    spec = SAMPLES[kind]
    for v in range(3):
        params = dict(spec["params"], variant=v)
        sc = Scene(id=f"{kind}{v}", kind=kind, narration=spec["narration"], params=params, hold_seconds=spec.get("hold_seconds", 0))
        r = get_renderer(kind)(sc, _ctx(cfg, fonts, size))
        g = r.g
        beats = r.plan()
        assert beats[0].kind == "lead"
        steps = sorted({b.step for b in beats})
        for st in steps:
            im = r.still(st)
            assert im.size == (g.W, g.H)
            for it in r.items:
                if not it.visible(st) or it.z < 0:
                    continue
                x, y, a, sp = it.props(st)
                if a <= 0.01:
                    continue
                assert x >= -2 and x + sp.w <= g.W + 2, f"{kind} v{v} 가로 밖 {it.name}"
                assert y >= g.top_safe - 2, f"{kind} v{v} 위 안전영역 침범"
                assert y + sp.h <= g.sub.y + 4, f"{kind} v{v} 단계 {st}: 요소가 자막 영역({g.sub.y})과 겹침 (bottom={y + sp.h:.0f})"
        # 단계 전환 중간 프레임도 그려진다
        prev = -1
        for st in steps:
            if st != prev:
                t = r.transition_seconds(prev, st)
                r.frame(prev, st, t / 2)
            prev = st
        r.release()


def test_variant_is_deterministic_and_overridable(cfg, fonts):
    from studio.video.scenes import get_renderer

    sc = Scene(id="x1", kind="text", narration=["a"], params={"body": "본문"})
    v1 = get_renderer("text")(sc, _ctx(cfg, fonts, (1920, 1080))).variant
    v2 = get_renderer("text")(sc, _ctx(cfg, fonts, (1920, 1080))).variant
    assert v1 == v2
    seen = {get_renderer("text")(Scene(id=f"x{i}", kind="text", narration=["a"], params={"body": "본문"}),
                                 _ctx(cfg, fonts, (1920, 1080))).variant for i in range(30)}
    assert seen == {0, 1, 2}
    forced = get_renderer("text")(Scene(id="x1", kind="text", narration=["a"], params={"body": "본문", "variant": 2}),
                                  _ctx(cfg, fonts, (1920, 1080))).variant
    assert forced == 2


def test_spread_mapping():
    from studio.video.scenes import SceneRenderer

    assert SceneRenderer.spread(3, 3) == [0, 1, 2]
    assert SceneRenderer.spread(3, 4, lead=1) == [1, 2, 3]
    assert SceneRenderer.spread(4, 2) == [0, 0, 1, 1]


def test_quiz_plan_phases(cfg, fonts):
    from studio.video.scenes import get_renderer

    spec = SAMPLES["quiz"]
    sc = Scene(id="q", kind="quiz", narration=spec["narration"], params=spec["params"], hold_seconds=6)
    r = get_renderer("quiz")(sc, _ctx(cfg, fonts, (1080, 1920)))
    beats = r.plan()
    kinds = [(b.kind, b.step, b.line) for b in beats]
    assert kinds[0] == ("lead", -1, None)
    assert kinds[1] == ("speech", 0, 0) and kinds[2] == ("speech", 1, 1)      # 문제 → 보기
    cd = beats[3]
    assert cd.kind == "hold" and cd.live and cd.sfx == "countdown" and cd.seconds == 6
    assert (beats[4].kind, beats[4].line, beats[4].step) == ("speech", 2, r.s_rev)   # '정답' 문장에서 공개
    assert beats[5].step == r.s_exp
    # 카운트다운 효과음은 hold 길이와 같다
    audio = r.hold_audio(cd, 6.0)
    assert audio is not None and len(audio) == 6 * 48000 and np.abs(audio).max() > 0
    # 카운트다운 위젯이 실제로 움직인다(프레임이 다름)
    a = r.still(r.s_c).copy()
    b = r.still(r.s_c).copy()
    r.live(a, cd, 0.5, 6.0)
    r.live(b, cd, 4.5, 6.0)
    assert a.tobytes() != b.tobytes()


def test_quiz_without_countdown_uses_default_and_warns(cfg, fonts):
    from studio.video.scenes import get_renderer
    from studio.video.scenes.quiz import DEFAULT_COUNTDOWN

    spec = SAMPLES["quiz"]
    r = get_renderer("quiz")(Scene(id="q", kind="quiz", narration=["문제"], params=spec["params"]), _ctx(cfg, fonts, (1920, 1080)))
    beats = r.plan()
    assert any(b.live and b.seconds == DEFAULT_COUNTDOWN for b in beats)
    assert r.warnings
    assert beats[-1].kind == "hold" and beats[-1].step == r.s_exp      # 해설 문장이 없으면 읽을 시간


def test_sequence_shuffles_and_sorts(cfg, fonts):
    from studio.video.scenes import get_renderer

    spec = SAMPLES["sequence"]
    r = get_renderer("sequence")(Scene(id="s", kind="sequence", narration=spec["narration"], params=spec["params"]),
                                 _ctx(cfg, fonts, (1080, 1920)))
    order = r._order()
    assert sorted(order) == [0, 1, 2] and order != [0, 1, 2]
    cards = [it for it in r.items if it.keys.get(1) and it.keys[1].y is not None]
    assert len(cards) == 3
    # 단계 1에서 정답 순서 자리로 이동
    ys = [it.props(1)[1] for it in cards]
    assert ys == sorted(ys)
    explicit = get_renderer("sequence")(Scene(id="s", kind="sequence", narration=["a"], params={"items": ["가", "나"], "shuffled": ["나", "가"]}),
                                        _ctx(cfg, fonts, (1080, 1920)))
    assert explicit._order() == [1, 0]


def test_image_and_voice_scenes(cfg, fonts, tmp_path):
    from studio.video.scenes import SceneError, get_renderer

    pic = tmp_path / "pic.png"
    Image.new("RGB", (800, 500), (90, 140, 170)).save(pic)
    for size in ((1920, 1080), (1080, 1920)):
        for v in range(3):
            r = get_renderer("image")(Scene(id="i", kind="image", narration=["a", "b"], params={"path": "pic.png", "caption": "설명", "variant": v}),
                                      _ctx(cfg, fonts, size, root=tmp_path))
            r.still(r.max_step())
    with pytest.raises(SceneError, match="파일이 없습니다"):
        get_renderer("image")(Scene(id="i", kind="image", params={"path": "none.png"}), _ctx(cfg, fonts, (1920, 1080), root=tmp_path))
    wav = tmp_path / "v.wav"
    wav.write_bytes(b"x")
    r = get_renderer("voice")(Scene(id="v", kind="voice", narration=["첫 문장입니다.", "두 번째 문장입니다."], params={"audio": "v.wav"}),
                              _ctx(cfg, fonts, (1080, 1920), root=tmp_path))
    sr = 48000
    t = np.arange(sr * 4) / sr
    samples = (np.sin(2 * np.pi * 200 * t) * 12000 * (t > 1)).astype(np.int16)
    r.prepare_voice(4.0, samples, sr)
    beats = r.plan()
    voice = [b for b in beats if b.kind == "voice"]
    assert len(voice) == 2 and all(b.live for b in voice) and r.own_captions
    assert abs(sum(b.seconds for b in voice) - (4.0 + 0.25)) < 1e-6
    quiet, loud = r.still(0).copy(), r.still(0).copy()
    r.live(quiet, voice[0], 0.2, 2.0)
    r.live(loud, voice[0], 1.8, 2.0)
    assert quiet.tobytes() != loud.tobytes()


# ---------------------------------------------------------------- 자막

def test_subtitle_chunks_and_srt(fonts, tmp_path):
    from studio.video.subtitles import chunk_sentence, sentence_cues, srt_time, write_srt

    long = ("되와 돼를 구분하는 가장 쉬운 방법은 그 자리에 되어를 넣어 읽어 보는 것인데, 자연스러우면 돼를 쓰고 "
            "어색하면 되를 쓰면 되니 오늘부터 꼭 한 번씩 해 보세요")
    chunks = chunk_sentence(long, fonts, 58, 860)
    assert len(chunks) >= 2
    font = fonts.get("bold", 58)
    for c in chunks:
        assert len(D.wrap_spans(c, font, 860)) <= 2
    assert " ".join(chunks) == long
    cues = sentence_cues(long, 1.0, 9.0, 9.25, fonts, 58, 860)
    assert cues[0].start == 1.0 and cues[-1].end == 9.25
    assert all(a.end <= b.start + 1e-9 for a, b in zip(cues, cues[1:]))
    assert srt_time(3725.5) == "01:02:05,500"
    p = write_srt(cues, tmp_path / "s.srt", fonts, 58, 860)
    txt = p.read_text(encoding="utf-8")
    assert txt.startswith("1\n00:00:01,000 --> ")
    emph = chunk_sentence("이것은 **아주 중요한 낱말** 입니다", fonts, 58, 2000)
    assert emph == ["이것은 **아주 중요한 낱말** 입니다"]


# ---------------------------------------------------------------- TTS

class _Counting:
    name, voice, rate = "fake", "v", 1.0

    def __init__(self, fail: int = 0, exc: Exception | None = None):
        self.calls = 0
        self.fail = fail
        self.exc = exc

    def synthesize(self, text, out_path):
        from studio.video import media

        self.calls += 1
        if self.calls <= self.fail:
            raise self.exc or ConnectionError("일시 오류")
        media.write_wav(out_path, np.zeros(48000, dtype=np.int16))
        return 1.0


def test_silent_tts_estimates_length(root):
    from studio.video.tts import SilentTTS

    t = SilentTTS(rate=1.0)
    secs = t.synthesize("안녕하세요. 오늘은 맞춤법을 정리합니다.", root / "a.wav")
    assert 3.0 < secs < 6.0
    assert SilentTTS.estimate("가") >= 0.8


def test_tts_cache_reuses_clip(root):
    from studio.video.tts import CachedTTS

    p = _Counting()
    c = CachedTTS(p)
    path1, s1 = c.clip("같은 문장입니다.")
    path2, s2 = c.clip("같은  문장입니다.")      # 공백 차이는 같은 문장
    assert p.calls == 1 and path1 == path2 and s1 == s2 == 1.0
    assert path1.parent == paths.OUT / "cache" / "tts"
    assert c.synthesize("같은 문장입니다.", root / "copy.wav") == 1.0 and (root / "copy.wav").exists()


def test_tts_retry_backoff(root):
    from studio.video.tts import TTSError, with_retry

    waits: list[float] = []
    p = _Counting(fail=2)
    assert with_retry(lambda: p.synthesize("x", root / "r.wav"), what="t", sleep=waits.append) == 1.0
    assert waits == [1.0, 2.0] and p.calls == 3
    p = _Counting(fail=5)
    with pytest.raises(TTSError, match="3회"):
        with_retry(lambda: p.synthesize("x", root / "r.wav"), what="t", sleep=waits.append)
    p = _Counting(fail=5, exc=TTSError("권한", retryable=False))
    with pytest.raises(TTSError):
        with_retry(lambda: p.synthesize("x", root / "r.wav"), what="t", sleep=lambda s: None)
    assert p.calls == 1


def _wav_bytes(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        t = np.arange(n) / rate
        sig = (np.sin(2 * np.pi * 300 * t) * 9000).astype(np.int16)
        pad = np.zeros(int(0.5 * rate), dtype=np.int16)
        w.writeframes(np.concatenate([pad, sig, pad]).tobytes())
    return buf.getvalue()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 필요")
def test_google_tts_mocked_rate_fallback(root, monkeypatch, caplog):
    from google.api_core import exceptions as gexc

    from studio.video import media
    from studio.video.tts import GoogleTTS

    requests = []

    class FakeClient:
        def synthesize_speech(self, input, voice, audio_config):
            requests.append(audio_config.speaking_rate)
            if audio_config.speaking_rate:
                raise gexc.InvalidArgument("This voice does not support speaking_rate.")

            class R:
                audio_content = _wav_bytes(1.0)
            return R()

    g = GoogleTTS("ko-KR-Chirp3-HD-Aoede", rate=0.95)
    monkeypatch.setattr(g, "client", lambda: FakeClient())
    with caplog.at_level("WARNING"):
        secs = g.synthesize("안녕하세요", root / "g.wav")
    assert requests[0] == pytest.approx(0.95) and requests[-1] == 0
    assert "speaking_rate" in caplog.text
    # 48kHz 모노로 바뀌고 앞뒤 무음 0.5초가 잘렸다
    assert 1.0 <= secs < 1.3
    assert media.read_wav(root / "g.wav").size == pytest.approx(secs * 48000, abs=2)
    g.synthesize("두 번째", root / "g2.wav")
    assert requests[-1] == 0 and len(requests) == 3     # 다음 요청부터는 속도를 보내지 않는다


def test_google_credentials_from_json_env(root, monkeypatch):
    from studio.video import tts as T

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_TTS_CREDENTIALS_JSON", raising=False)
    monkeypatch.setattr(T, "_TEMP_CREDENTIALS", None)
    with pytest.raises(MissingSecret, match="GOOGLE_TTS_CREDENTIALS_JSON"):
        T.google_credentials_file()
    monkeypatch.setenv("GOOGLE_TTS_CREDENTIALS_JSON", "{not json")
    with pytest.raises(MissingSecret, match="JSON"):
        T.google_credentials_file()
    monkeypatch.setenv("GOOGLE_TTS_CREDENTIALS_JSON", json.dumps({"type": "service_account", "project_id": "p"}))
    p = T.google_credentials_file()
    assert p.exists() and json.loads(p.read_text())["project_id"] == "p"
    assert (p.stat().st_mode & 0o777) == 0o600


def test_tts_resolve_rules(root, monkeypatch, cfg, capsys):
    from studio.video import tts as T

    monkeypatch.setenv("STUDIO_TTS", "silent")
    assert T.resolve(cfg).name == "silent"
    monkeypatch.delenv("STUDIO_TTS")
    assert T.resolve(cfg).name == "google"
    assert T.resolve(cfg, dry_run=True).name == "silent"
    assert "dry-run" in capsys.readouterr().out
    assert T.resolve(cfg, "silent").name == "silent"
    none_cfg = cfg.model_copy(deep=True)
    none_cfg.tts.fallback = "none"
    assert T.fallback(none_cfg, T.resolve(none_cfg)) is None
    edge_cfg = cfg.model_copy(deep=True)
    edge_cfg.tts.fallback = "edge"
    fb = T.fallback(edge_cfg, T.resolve(edge_cfg))
    assert fb is not None and fb.name == "edge"
    assert "약관" in capsys.readouterr().out
    with pytest.raises(T.TTSError):
        T.resolve(cfg, "elevenlabs")


# ---------------------------------------------------------------- 오디오 배치·진행 막대

def test_build_narration_places_clips_on_frames(root):
    from studio.video import media
    from studio.video.compose import TBeat, build_narration
    from studio.video.scenes import Beat

    clip = root / "c.wav"
    media.write_wav(clip, np.full(4800, 1000, dtype=np.int16))       # 0.1초
    beats = [TBeat(0, Beat("lead", -1), start=0, frames=10),
             TBeat(0, Beat("speech", 0, line=0), start=10, frames=20, clip=clip, clip_secs=0.1),
             TBeat(0, Beat("hold", 0, seconds=1), start=30, frames=30, audio=np.full(100, 7, dtype=np.int16))]
    out = build_narration(beats, 60, 30, root / "n.wav")
    data = media.read_wav(out)
    assert data.size == 60 * 1600
    assert data[:16000].max() == 0 and data[16000] == 1000 and data[16000 + 4799] == 1000 and data[16000 + 4800] == 0
    assert data[48000] == 7


def test_progress_units_fit_track(cfg):
    from studio.video.compose import Progress

    g = D.Geometry.make((1080, 1920))
    pr = Progress(g, D.Palette.from_brand(cfg.brand), [(0, 100), (100, 300), (400, 50)], 450)
    xs = [(x, x + w) for _, _, x, w in pr.units]
    assert xs[0][0] == g.progress.x and xs[-1][1] == g.progress.x + g.progress.w
    assert all(a[1] < b[0] for a, b in zip(xs, xs[1:]))
    m = pr.motion(150)
    assert m["off"] == 50 and m["unit"] == 300


# ---------------------------------------------------------------- 썸네일

def _script(fmt: str, th: Thumbnail | None) -> VideoScript:
    return VideoScript(id="2026W42-L1" if fmt == "longform" else "2026W42-S01", week="2026-W42", format=fmt, title="제목",
                       description="설명", chapters=[{"title": "장", "scenes": [{"id": "a", "kind": "title", "params": {"title": "t"}}]}],
                       thumbnail=th)


@pytest.mark.parametrize("style", ["bold", "split", "card", "photo", "default"])
def test_thumbnail_styles(root, cfg, style):
    from studio.video.thumbnail import render_thumbnail

    Image.new("RGB", (900, 900), (120, 160, 200)).save(root / "photo.jpg")
    th = Thumbnail(headline="되/돼 아직도 헷갈리세요?", subline="원리 하나로 끝내기", style=style,
                   params={"highlight": "되/돼", "big": "3분", "badge": "총정리", "image": "photo.jpg"})
    out = render_thumbnail(_script("longform", th), cfg, root / f"t_{style}.png")
    with Image.open(out) as im:
        assert im.size == (1280, 720) and im.format == "PNG"
    assert out.stat().st_size <= 2 * 1024 * 1024


@pytest.mark.parametrize("style", ["bold", "block", "frame"])
def test_cover_styles(root, cfg, style):
    from studio.video.thumbnail import render_cover

    out = render_cover(_script("short", Thumbnail(headline="안 되요? 안 돼요?", subline="3초 구분법", style=style)), cfg,
                       root / f"c_{style}.png")
    with Image.open(out) as im:
        assert im.size == (1080, 1920)
    assert render_cover(_script("short", None), cfg, root / "c_none.png").exists()
