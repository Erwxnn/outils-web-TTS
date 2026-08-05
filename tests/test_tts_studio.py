from pathlib import Path

from services.tts_studio import EdgeTTSEngine, TTSRequest, Voice, ssml_to_plain_text


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


def test_edge_engine_converts_ssml_before_synthesizing(monkeypatch, tmp_path):
    captured = {}

    class FakeCommunicate:
        def __init__(self, text, voice, **kwargs):
            captured["text"] = text

        async def save(self, path):
            Path(path).write_bytes(b"mp3")

    monkeypatch.setattr("edge_tts.Communicate", FakeCommunicate)

    request = TTSRequest(
        text='<speak><break time="300ms"/>Hi</speak>',
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=0,
        volume=0,
    )

    output = EdgeTTSEngine().synthesize(request, tmp_path / "out.mp3")

    assert output.read_bytes() == b"mp3"
    assert "<" not in captured["text"]


def test_voice_label_and_search_text():
    voice = Voice(id="en-US-AriaNeural", name="Aria", locale="en-US", gender="Female")

    assert voice.label == "Aria - en-US - Female"
    assert "aria" in voice.search_text
    assert "en-us" in voice.search_text
