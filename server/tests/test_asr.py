from __future__ import annotations

import httpx
import numpy as np
import pytest

from quill_server.asr import EngineError, Utterance, Word
from quill_server.asr.openai_remote import OpenAIEngine, utterances_from_verbose
from quill_server.asr.parakeet import ParakeetEngine, words_from_tokens
from quill_server.asr.worker import LIVE, OFFLINE, RecognizerWorker
from quill_server.audio import SAMPLE_RATE, SpeechCutter, decode_pcm16
from quill_server.config import Config
from quill_server.models import ModelManager

from .conftest import MODELS_DIR, PARAKEET, TEST_WAV, FakeEngine, requires_parakeet, requires_vad


def test_words_from_tokens_groups_on_leading_space():
    tokens = [" A", "sk", " not", " what", " co", "un", "tr", "y", ".", " Ok"]
    ts = [0.0, 0.24, 0.4, 0.64, 0.96, 1.04, 1.12, 1.2, 1.28, 1.4]
    du = [0.24, 0.16, 0.24, 0.16, 0.08, 0.08, 0.08, 0.08, 0.08, 0.16]
    words = words_from_tokens(tokens, ts, du)
    assert [w.w for w in words] == ["Ask", "not", "what", "country.", "Ok"]
    assert words[0] == Word("Ask", 0, 400)  # ends where its last token ends
    assert words[3].s == 960 and words[3].e == 1360


def test_words_without_durations_end_at_next_word():
    words = words_from_tokens([" a", " b"], [0.0, 0.5], None)
    assert words == [Word("a", 0, 500), Word("b", 500, 580)]


def test_words_empty():
    assert words_from_tokens([], [], None) == []


def test_openai_engine_posts_verbose_json_and_uses_segments():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        body = request.content
        seen["has_format"] = b'name="response_format"\r\n\r\nverbose_json' in body
        seen["model"] = b'name="model"\r\n\r\nwhisper-x' in body
        seen["lang"] = b'name="language"\r\n\r\nde' in body
        return httpx.Response(200, json={"text": "a b", "segments": [
            {"start": 0.0, "end": 1.5, "text": " Hallo "}, {"start": 1.5, "end": 2.0, "text": "Welt"}]})  # fmt: skip

    engine = OpenAIEngine("http://asr:8081/", "k", "whisper-x", client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = engine.transcribe(np.zeros(SAMPLE_RATE * 2, dtype=np.float32), language="de")
    assert seen == {"url": "http://asr:8081/v1/audio/transcriptions", "auth": "Bearer k", "has_format": True,
                    "model": True, "lang": True}  # fmt: skip
    assert [(u.start_ms, u.end_ms, u.text) for u in out] == [(0, 1500, "Hallo"), (1500, 2000, "Welt")]


def test_ollama_text_only_response_becomes_one_segment_per_cut():
    """Ollama's /v1/audio/transcriptions answers ``{"text"}`` only, even for verbose_json."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["model"] = b'name="model"\r\n\r\ngemma4:e2b' in request.content
        return httpx.Response(200, json={"text": "Hello there.\n"})

    engine = OpenAIEngine("http://172.18.0.1:11434", "", "gemma4:e2b",
                          client=httpx.Client(transport=httpx.MockTransport(handler)))  # fmt: skip
    out = engine.transcribe(np.zeros(SAMPLE_RATE * 3, dtype=np.float32))
    assert seen == {"url": "http://172.18.0.1:11434/v1/audio/transcriptions", "auth": None, "model": True}
    assert out == [Utterance(0, 3000, "Hello there.", [])]


def test_openai_engine_single_segment_fallback_and_errors():
    ok = OpenAIEngine("http://x", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"text": " hi "}))))
    out = ok.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
    assert out == [Utterance(0, 1000, "hi", [])]
    bad = OpenAIEngine("http://x", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(EngineError):
        bad.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))


def test_verbose_words_are_attached_to_their_segment():
    doc = {"segments": [{"start": 0, "end": 1, "text": "a b"}, {"start": 1, "end": 2, "text": "c"}],
           "words": [{"word": "a", "start": 0, "end": 0.4}, {"word": "b", "start": 0.5, "end": 0.9},
                     {"word": "c", "start": 1.1, "end": 1.5}]}  # fmt: skip
    segs = utterances_from_verbose(doc, 2000)
    assert [len(s.words) for s in segs] == [2, 1]


def test_worker_serializes_and_prioritizes_live():
    order = []

    class Slow(FakeEngine):
        def transcribe(self, samples, language=None):
            order.append(len(samples))
            return super().transcribe(samples, language)

    w = RecognizerWorker(Slow())
    # queue before starting so priorities, not arrival, decide
    f_off = w.submit(np.zeros(16000, dtype=np.float32), priority=OFFLINE)
    f_live = w.submit(np.zeros(32000, dtype=np.float32), priority=LIVE)
    w.start()
    f_live.result(5), f_off.result(5)
    w.stop()
    assert order == [32000, 16000]


def test_worker_propagates_errors():
    class Boom(FakeEngine):
        def transcribe(self, samples, language=None):
            raise EngineError("nope")

    w = RecognizerWorker(Boom())
    w.start()
    with pytest.raises(EngineError):
        w.submit(np.zeros(16000, dtype=np.float32)).result(5)
    w.stop()


@requires_vad
def test_vad_cuts_speech_and_flush_emits_tail():
    cutter = SpeechCutter(MODELS_DIR / "silero_vad.onnx")
    pcm = decode_pcm16(TEST_WAV)
    cuts = []
    for i in range(0, len(pcm), 1600):
        cuts += cutter.feed(pcm[i : i + 1600])
    cuts += cutter.flush()
    assert len(cuts) == 1
    assert 3.0 < len(cuts[0].samples) / SAMPLE_RATE < 4.0 and cuts[0].samples.dtype == np.float32


@requires_parakeet
def test_parakeet_real_model_word_timings():
    config = Config(models_dir=MODELS_DIR, asr_model=PARAKEET, asr_threads=4)
    engine = ParakeetEngine(ModelManager(config), PARAKEET, 4)
    assert engine.ready()
    pcm = decode_pcm16(TEST_WAV).astype(np.float32) / 32768
    (utt,) = engine.transcribe(pcm)
    assert utt.text == "Ask not what your country can do for you, ask what you can do for your country."
    assert [w.w for w in utt.words][:3] == ["Ask", "not", "what"]
    assert all(a.e <= b.s + 1 or a.s <= b.s for a, b in zip(utt.words, utt.words[1:]))
    assert utt.words[-1].w == "country." and utt.end_ms == utt.words[-1].e
