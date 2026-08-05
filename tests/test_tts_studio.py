from pathlib import Path

import pytest

from services.tts_studio import EdgeTTSEngine, TTSRequest, WindowsSapiEngine


def test_windows_sapi_uses_ssml_method_for_ssml(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_system() -> str:
        return "Windows"

    def fake_run_powershell(command: str, *args: str):
        calls.append((command, args))
        Path(args[1]).write_bytes(b"wav")

        class Result:
            stdout = ""

        return Result()

    monkeypatch.setattr("services.tts_studio.platform.system", fake_system)
    monkeypatch.setattr("services.tts_studio._run_powershell", fake_run_powershell)
    request = TTSRequest(
        engine="windows",
        text="<speak>Hello</speak>",
        text_mode="ssml",
        voice_id="Microsoft David Desktop",
        rate=0,
        volume=100,
    )

    output = WindowsSapiEngine().synthesize(request, tmp_path / "out.wav")

    assert output.read_bytes() == b"wav"
    assert ".SpeakSsml(" in calls[0][0]


def test_edge_engine_rejects_ssml() -> None:
    request = TTSRequest(
        engine="edge",
        text="<speak>Hello</speak>",
        text_mode="ssml",
        voice_id="en-US-AriaNeural",
        rate=0,
        volume=0,
    )

    with pytest.raises(ValueError, match="SSML"):
        EdgeTTSEngine().synthesize(request, Path("out.mp3"))
