from pathlib import Path

import pytest

from services.tts_studio import (
    BreakSegment,
    EdgeTTSEngine,
    TTSCancelled,
    TTSRequest,
    TextSegment,
    Voice,
    _silence_bytes,
    parse_ssml_segments,
    ssml_to_plain_text,
)


def test_ssml_to_plain_text_approximates_breaks_and_strips_tags():
    ssml = '<speak><p>Hello.</p><break time="600ms"/><p>World.</p></speak>'

    text = ssml_to_plain_text(ssml)

    assert "<" not in text
    assert "Hello." in text
    assert "World." in text
    assert "," in text


def test_ssml_to_plain_text_supports_seconds_unit():
    text = ssml_to_plain_text('Hi<break time="1s"/>there')

    assert "<" not in text
    assert "Hi" in text and "there" in text


def test_parse_ssml_segments_orders_text_breaks_and_prosody_rate():
    ssml = (
        "Day one.\n"
        '<break time="1s"/>\n'
        '<prosody rate="slow">Be. Was. Were. Been.</prosody>\n'
        '<break time="4s"/>\n'
        "Again."
    )

    segments = parse_ssml_segments(ssml)

    assert segments == [
        TextSegment(text="Day one.", rate_delta=0),
        BreakSegment(duration_ms=1000),
        TextSegment(text="Be. Was. Were. Been.", rate_delta=-25),
        BreakSegment(duration_ms=4000),
        TextSegment(text="Again.", rate_delta=0),
    ]


def test_parse_ssml_segments_ignores_zero_length_breaks():
    segments = parse_ssml_segments('Hello<break time="0s"/>world')

    assert all(not isinstance(s, BreakSegment) for s in segments)


def test_parse_ssml_segments_strips_leading_bom():
    segments = parse_ssml_segments("﻿Day one.")

    assert segments == [TextSegment(text="Day one.", rate_delta=0)]


def test_parse_ssml_segments_falls_back_on_malformed_xml():
    segments = parse_ssml_segments('Rock & Roll <break time="1s"/> forever')

    assert len(segments) == 1
    assert isinstance(segments[0], TextSegment)
    assert "<" not in segments[0].text


def test_silence_bytes_length_matches_requested_duration():
    assert _silence_bytes(0) == b""
    assert len(_silence_bytes(1000)) == round(1000 / 24) * 144
    assert len(_silence_bytes(700)) == round(700 / 24) * 144


class _FakeCommunicate:
    """Fakes edge_tts.Communicate.stream() with sentence-boundary + audio chunks."""

    def __init__(self, text, voice, **kwargs):
        self._text = text

    async def stream(self):
        half = max(1, len(self._text) // 2)
        yield {"type": "SentenceBoundary", "text": self._text[:half]}
        yield {"type": "audio", "data": b"abc"}
        yield {"type": "SentenceBoundary", "text": self._text[half:]}
        yield {"type": "audio", "data": b"def"}


def test_edge_engine_reports_progress_up_to_completion(monkeypatch, tmp_path):
    monkeypatch.setattr("edge_tts.Communicate", _FakeCommunicate)

    progress_values: list[float] = []
    request = TTSRequest(text="Hello world", text_mode="plain", voice_id="en-US-AriaNeural", rate=0, volume=0)

    output = EdgeTTSEngine().synthesize(
        request, tmp_path / "out.mp3", on_progress=progress_values.append
    )

    assert output.read_bytes() == b"abcdef"
    assert progress_values == sorted(progress_values)
    assert progress_values[-1] == 1.0


def test_edge_engine_cancellation_cleans_up_partial_file(monkeypatch, tmp_path):
    monkeypatch.setattr("edge_tts.Communicate", _FakeCommunicate)

    request = TTSRequest(text="Hello world", text_mode="plain", voice_id="en-US-AriaNeural", rate=0, volume=0)
    output_path = tmp_path / "out.mp3"

    with pytest.raises(TTSCancelled):
        EdgeTTSEngine().synthesize(request, output_path, should_cancel=lambda: True)

    assert not output_path.exists()


def _recording_communicate(calls_log):
    class _Recorder:
        def __init__(self, text, voice, **kwargs):
            calls_log.append({"text": text, "voice": voice, **kwargs})

        async def stream(self):
            yield {"type": "audio", "data": b"S"}

    return _Recorder


def test_edge_engine_ssml_inserts_real_silence_and_applies_prosody_rate(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr("edge_tts.Communicate", _recording_communicate(calls))

    request = TTSRequest(
        text='Hello.<break time="500ms"/><prosody rate="slow">Slow part.</prosody>',
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=0,
        volume=0,
    )

    progress_values: list[float] = []
    output = EdgeTTSEngine().synthesize(
        request, tmp_path / "out.mp3", on_progress=progress_values.append
    )

    expected = b"S" + _silence_bytes(500) + b"S"
    assert output.read_bytes() == expected
    assert len(calls) == 2
    assert calls[0]["rate"] == "+0%"
    assert calls[1]["rate"] == "-25%"
    assert progress_values[-1] == 1.0


def test_edge_engine_ssml_combines_base_rate_with_prosody_delta(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr("edge_tts.Communicate", _recording_communicate(calls))

    request = TTSRequest(
        text='<prosody rate="fast">Quicker.</prosody>',
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=10,
        volume=0,
    )

    EdgeTTSEngine().synthesize(request, tmp_path / "out.mp3")

    assert calls[0]["rate"] == "+35%"  # base +10 combined with "fast" (+25)


def test_edge_engine_ssml_cancellation_cleans_up_partial_file(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr("edge_tts.Communicate", _recording_communicate(calls))

    request = TTSRequest(
        text='One.<break time="1s"/>Two.<break time="1s"/>Three.',
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=0,
        volume=0,
    )
    output_path = tmp_path / "out.mp3"

    checks = {"n": 0}

    def should_cancel() -> bool:
        checks["n"] += 1
        return checks["n"] > 2

    with pytest.raises(TTSCancelled):
        EdgeTTSEngine().synthesize(request, output_path, should_cancel=should_cancel)

    assert not output_path.exists()
    assert len(calls) < 3  # cancelled before all three text segments ran


def test_edge_engine_ssml_falls_back_gracefully_on_malformed_input(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr("edge_tts.Communicate", _recording_communicate(calls))

    request = TTSRequest(
        text='Rock & Roll <break time="1s"/> forever',
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=0,
        volume=0,
    )

    output = EdgeTTSEngine().synthesize(request, tmp_path / "out.mp3")

    assert output.read_bytes() == b"S"
    assert len(calls) == 1
    assert "<" not in calls[0]["text"]


def test_voice_label_and_search_text():
    voice = Voice(id="en-US-AriaNeural", name="Aria", locale="en-US", gender="Female")

    assert voice.label == "Aria - en-US - Female"
    assert "aria" in voice.search_text
    assert "en-us" in voice.search_text
