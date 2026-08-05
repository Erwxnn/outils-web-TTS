"""General-purpose TTS engines for the Streamlit studio."""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


TextMode = Literal["plain", "ssml"]
EngineName = Literal["windows", "edge"]


@dataclass(frozen=True)
class Voice:
    """A voice available in a TTS engine."""

    engine: EngineName
    id: str
    name: str
    locale: str
    gender: str = ""
    description: str = ""

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.locale:
            parts.append(self.locale)
        if self.gender:
            parts.append(self.gender)
        return " - ".join(parts)


@dataclass(frozen=True)
class TTSRequest:
    """A synthesis request from the UI."""

    engine: EngineName
    text: str
    text_mode: TextMode
    voice_id: str
    rate: int
    volume: int
    pitch: int = 0


class WindowsSapiEngine:
    """Text-to-speech engine using native Windows SAPI voices."""

    def list_voices(self) -> list[Voice]:
        if platform.system() != "Windows":
            return []

        command = (
            "Add-Type -AssemblyName System.Speech; "
            "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$voices = $synth.GetInstalledVoices() | ForEach-Object { "
            "$info = $_.VoiceInfo; "
            "[PSCustomObject]@{ "
            "Id = $info.Name; "
            "Name = $info.Name; "
            "Locale = $info.Culture.Name; "
            "Gender = [string]$info.Gender; "
            "Description = $info.Description; "
            "Enabled = $_.Enabled "
            "} "
            "}; "
            "$synth.Dispose(); "
            "$voices | ConvertTo-Json -Depth 4"
        )
        result = _run_powershell(command)
        if not result.stdout.strip():
            return []

        payload = json.loads(result.stdout)
        rows = payload if isinstance(payload, list) else [payload]
        voices = [
            Voice(
                engine="windows",
                id=str(row.get("Id", "")),
                name=str(row.get("Name", "")),
                locale=str(row.get("Locale", "")),
                gender=str(row.get("Gender", "")),
                description=str(row.get("Description", "")),
            )
            for row in rows
            if row.get("Enabled", True)
        ]
        return voices

    def synthesize(self, request: TTSRequest, output_path: Path) -> Path:
        if platform.system() != "Windows":
            raise RuntimeError("Windows SAPI is available only on Windows.")

        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        input_path = output_path.with_suffix(".input.xml" if request.text_mode == "ssml" else ".input.txt")
        input_path.write_text(request.text, encoding="utf-8")
        method = "SpeakSsml" if request.text_mode == "ssml" else "Speak"
        if not request.voice_id:
            raise ValueError("Choose a Windows voice before generating audio.")
        escaped_voice = request.voice_id.replace("'", "''")
        voice_command = f"$synth.SelectVoice('{escaped_voice}'); "
        command = (
            "Add-Type -AssemblyName System.Speech; "
            "$inputText = Get-Content -LiteralPath $args[0] -Raw; "
            "$outputPath = $args[1]; "
            "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"{voice_command}"
            f"$synth.Rate = {request.rate}; "
            f"$synth.Volume = {request.volume}; "
            "$synth.SetOutputToWaveFile($outputPath); "
            f"$synth.{method}($inputText); "
            "$synth.Dispose();"
        )
        try:
            _run_powershell(command, str(input_path), str(output_path))
        finally:
            input_path.unlink(missing_ok=True)
        return output_path


class EdgeTTSEngine:
    """Online Microsoft Edge voice engine via the edge-tts package."""

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False
        return True

    def list_voices(self) -> list[Voice]:
        if not self.is_available():
            return []
        try:
            return asyncio.run(self._list_voices())
        except Exception:
            return []

    async def _list_voices(self) -> list[Voice]:
        import edge_tts

        rows: list[dict[str, Any]] = await edge_tts.list_voices()
        return [
            Voice(
                engine="edge",
                id=str(row.get("ShortName", "")),
                name=str(row.get("FriendlyName") or row.get("ShortName", "")),
                locale=str(row.get("Locale", "")),
                gender=str(row.get("Gender", "")),
                description=str(row.get("ShortName", "")),
            )
            for row in rows
        ]

    def synthesize(self, request: TTSRequest, output_path: Path) -> Path:
        if request.text_mode == "ssml":
            raise ValueError("SSML mode is supported by Windows voices only.")
        if not self.is_available():
            raise RuntimeError("edge-tts is not installed.")

        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._synthesize(request, output_path))
        return output_path

    async def _synthesize(self, request: TTSRequest, output_path: Path) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(
            request.text,
            request.voice_id,
            rate=f"{request.rate:+d}%",
            volume=f"{request.volume:+d}%",
            pitch=f"{request.pitch:+d}Hz",
        )
        await communicate.save(str(output_path))


class TTSStudio:
    """Facade used by the UI for voice discovery and synthesis."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        self.windows = WindowsSapiEngine()
        self.edge = EdgeTTSEngine()

    def list_voices(self, engine: EngineName) -> list[Voice]:
        if engine == "windows":
            return self.windows.list_voices()
        return self.edge.list_voices()

    def synthesize(self, request: TTSRequest) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        extension = "wav" if request.engine == "windows" else "mp3"
        output_path = self.output_dir / f"tts-{timestamp}.{extension}"
        if request.engine == "windows":
            return self.windows.synthesize(request, output_path)
        return self.edge.synthesize(request, output_path)


def _run_powershell(command: str, *args: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = Path(temp_dir) / "tts_command.ps1"
        script_path.write_text(command, encoding="utf-8")
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
