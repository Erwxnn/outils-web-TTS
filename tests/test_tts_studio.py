from pathlib import Path

import pytest

from services.tts_studio import (
    BreakSegment,
    EdgeTTSEngine,
    TTSCancelled,
    TTSRequest,
    TextSegment,
    Voice,
    VoiceValidationError,
    _silence_bytes,
    collect_request_voice_ids,
    filter_voices,
    list_countries,
    list_genders,
    list_languages,
    parse_friendly_name,
    parse_ssml_segments,
    ssml_to_plain_text,
    validate_voice_ids,
    voice_from_row,
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


def test_parse_ssml_segments_applies_voice_override():
    ssml = (
        '<voice name="fr-FR-HenriNeural">Saigner.</voice>\n'
        '<break time="200ms"/>\n'
        "Bleed."
    )

    segments = parse_ssml_segments(ssml)

    assert segments == [
        TextSegment(text="Saigner.", rate_delta=0, voice_id="fr-FR-HenriNeural"),
        BreakSegment(duration_ms=200),
        TextSegment(text="Bleed.", rate_delta=0, voice_id=None),
    ]


def test_parse_ssml_segments_voice_override_combines_with_prosody_rate():
    ssml = '<voice name="fr-FR-HenriNeural"><prosody rate="slow">Doucement.</prosody></voice>'

    segments = parse_ssml_segments(ssml)

    assert segments == [
        TextSegment(text="Doucement.", rate_delta=-25, voice_id="fr-FR-HenriNeural"),
    ]


def test_parse_ssml_segments_keeps_voice_after_a_nested_break():
    """Text following a <break> inside a <voice> stays on the overridden voice."""
    ssml = '<voice name="fr-FR-HenriNeural">Obtenir.<break time="200ms"/>Se procurer.</voice>'

    segments = parse_ssml_segments(ssml)

    assert segments == [
        TextSegment(text="Obtenir.", rate_delta=0, voice_id="fr-FR-HenriNeural"),
        BreakSegment(duration_ms=200),
        TextSegment(text="Se procurer.", rate_delta=0, voice_id="fr-FR-HenriNeural"),
    ]


def test_parse_ssml_segments_keeps_prosody_rate_after_a_nested_break():
    """Same inheritance rule for <prosody>: the rate survives a nested break."""
    ssml = '<prosody rate="slow">Un.<break time="200ms"/>Deux.</prosody>'

    segments = parse_ssml_segments(ssml)

    assert segments == [
        TextSegment(text="Un.", rate_delta=-25),
        BreakSegment(duration_ms=200),
        TextSegment(text="Deux.", rate_delta=-25),
    ]


def test_parse_ssml_segments_does_not_leak_voice_past_the_closing_tag():
    """Text after </voice> must fall back to the base voice."""
    ssml = '<voice name="fr-FR-HenriNeural">Saigner.</voice><break time="200ms"/>Bleed.'

    segments = parse_ssml_segments(ssml)

    assert segments == [
        TextSegment(text="Saigner.", rate_delta=0, voice_id="fr-FR-HenriNeural"),
        BreakSegment(duration_ms=200),
        TextSegment(text="Bleed.", rate_delta=0, voice_id=None),
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


def test_edge_engine_ssml_uses_voice_override_and_falls_back_to_base_voice(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr("edge_tts.Communicate", _recording_communicate(calls))

    request = TTSRequest(
        text='<voice name="fr-FR-HenriNeural">Saigner.</voice><break time="200ms"/>Bleed.',
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=0,
        volume=0,
    )

    EdgeTTSEngine().synthesize(request, tmp_path / "out.mp3")

    assert len(calls) == 2
    assert calls[0]["text"] == "Saigner."
    assert calls[0]["voice"] == "fr-FR-HenriNeural"
    assert calls[1]["text"] == "Bleed."
    assert calls[1]["voice"] == "en-US-AriaNeural"


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

    assert voice.label == "Aria - en-US - Female - en-US-AriaNeural"
    assert "aria" in voice.search_text
    assert "en-us" in voice.search_text
    # The ShortName must be searchable: it is what <voice name="..."> expects.
    assert "en-us-arianeural" in voice.search_text


# ---------------------------------------------------------------------------
# Voice metadata, browsing filters and validation
# ---------------------------------------------------------------------------


def _voice(short_name, locale, friendly_tail, gender="Female"):
    """Build a Voice the way voice_from_row would, from an Edge-style row."""
    return voice_from_row(
        {
            "ShortName": short_name,
            "FriendlyName": f"Microsoft X Online (Natural) - {friendly_tail}",
            "Locale": locale,
            "Gender": gender,
        }
    )


@pytest.fixture
def sample_voices():
    return [
        _voice("en-US-AriaNeural", "en-US", "English (United States)", "Female"),
        _voice("en-US-AndrewMultilingualNeural", "en-US", "English (United States)", "Male"),
        _voice("en-GB-RyanNeural", "en-GB", "English (United Kingdom)", "Male"),
        _voice("fr-FR-HenriNeural", "fr-FR", "French (France)", "Male"),
        _voice("fr-CA-SylvieNeural", "fr-CA", "French (Canada)", "Female"),
    ]


def test_parse_friendly_name_extracts_language_and_country():
    assert parse_friendly_name(
        "Microsoft Ava Online (Natural) - English (United States)"
    ) == ("English", "United States")


def test_parse_friendly_name_returns_empty_on_unexpected_shape():
    assert parse_friendly_name("Aria") == ("", "")
    assert parse_friendly_name("Microsoft Aria Online (Natural)") == ("", "")


def test_voice_falls_back_to_locale_subtags_when_friendly_name_is_unusable():
    """A FriendlyName Microsoft may change shape on must not break the filters."""
    voice = voice_from_row(
        {"ShortName": "fr-FR-HenriNeural", "FriendlyName": "Henri", "Locale": "fr-FR"}
    )

    assert voice.language_display == "fr"
    assert voice.country_display == "FR"


def test_voice_short_display_strips_locale_prefix_and_neural_suffix():
    voice = _voice("en-US-AndrewMultilingualNeural", "en-US", "English (United States)")

    assert voice.short_display == "AndrewMultilingual"


def test_voice_is_multilingual_detection():
    multi = _voice("en-US-AndrewMultilingualNeural", "en-US", "English (United States)")
    plain = _voice("en-US-AriaNeural", "en-US", "English (United States)")

    assert multi.is_multilingual
    assert not plain.is_multilingual


def test_list_languages_and_countries(sample_voices):
    assert list_languages(sample_voices) == ["English", "French"]
    assert list_countries(sample_voices) == [
        "Canada",
        "France",
        "United Kingdom",
        "United States",
    ]
    assert list_countries(sample_voices, language="French") == ["Canada", "France"]
    assert list_genders(sample_voices) == ["Female", "Male"]


def test_filter_voices_by_language_and_country(sample_voices):
    result = filter_voices(sample_voices, language="French", country="Canada")

    assert [v.id for v in result] == ["fr-CA-SylvieNeural"]


def test_filter_voices_by_gender_and_multilingual(sample_voices):
    result = filter_voices(sample_voices, gender="Male", multilingual_only=True)

    assert [v.id for v in result] == ["en-US-AndrewMultilingualNeural"]


def test_filter_voices_query_matches_terms_in_any_order(sample_voices):
    assert [v.id for v in filter_voices(sample_voices, query="andrew multi")] == [
        "en-US-AndrewMultilingualNeural"
    ]
    # A country name typed into the search box works too.
    assert [v.id for v in filter_voices(sample_voices, query="canada")] == [
        "fr-CA-SylvieNeural"
    ]


def test_filter_voices_without_criteria_returns_everything(sample_voices):
    assert filter_voices(sample_voices) == sample_voices


def test_collect_request_voice_ids_dedupes_and_keeps_base_voice_first():
    text = (
        '<voice name="fr-FR-HenriNeural">Saigner.</voice>'
        "Bleed."
        '<voice name="fr-FR-HenriNeural">Nourrir.</voice>'
        '<voice name="fr-CA-SylvieNeural">Manger.</voice>'
    )

    assert collect_request_voice_ids(text, "ssml", "en-US-AriaNeural") == [
        "en-US-AriaNeural",
        "fr-FR-HenriNeural",
        "fr-CA-SylvieNeural",
    ]


def test_collect_request_voice_ids_ignores_markup_in_plain_mode():
    text = '<voice name="fr-FR-HenriNeural">Saigner.</voice>'

    assert collect_request_voice_ids(text, "plain", "en-US-AriaNeural") == [
        "en-US-AriaNeural"
    ]


def test_validate_voice_ids_accepts_well_formed_and_known_names():
    validate_voice_ids(
        ["en-US-AndrewMultilingualNeural", "fr-FR-HenriNeural"],
        known_voice_ids=["en-US-AndrewMultilingualNeural", "fr-FR-HenriNeural"],
    )


def test_validate_voice_ids_rejects_malformed_names():
    for bad in ("fr-FR-Henri", "FR-fr-HenriNeural", "fr-fr-henrineural", "French Voice"):
        with pytest.raises(VoiceValidationError) as excinfo:
            validate_voice_ids([bad])
        assert bad in str(excinfo.value)


def test_validate_voice_ids_rejects_unknown_name_and_suggests_a_correction():
    with pytest.raises(VoiceValidationError) as excinfo:
        validate_voice_ids(
            ["fr-FR-HenryNeural"], known_voice_ids=["fr-FR-HenriNeural", "en-US-AriaNeural"]
        )

    message = str(excinfo.value)
    assert "fr-FR-HenryNeural" in message
    assert "fr-FR-HenriNeural" in message  # the suggestion


def test_validate_voice_ids_skips_existence_check_without_a_known_list():
    """Shape-only validation must not require network access."""
    validate_voice_ids(["fr-FR-TotallyMadeUpNeural"])


def test_studio_synthesize_rejects_unknown_voice_before_writing_anything(monkeypatch, tmp_path):
    """A bad <voice name="..."> must not leave a truncated file behind."""
    from services.tts_studio import TTSStudio

    calls: list[dict] = []
    monkeypatch.setattr("edge_tts.Communicate", _recording_communicate(calls))

    studio = TTSStudio(tmp_path)
    request = TTSRequest(
        text='Hello.<voice name="fr-FR-NopeNeural">Bonjour.</voice>',
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=0,
        volume=0,
    )

    with pytest.raises(VoiceValidationError):
        studio.synthesize(
            request,
            filename="out.mp3",
            known_voice_ids=["en-US-AriaNeural", "fr-FR-HenriNeural"],
        )

    assert calls == []  # nothing was sent to the service
    assert not (tmp_path / "out.mp3").exists()
