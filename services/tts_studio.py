"""Edge TTS engine for the Streamlit studio."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


TextMode = Literal["plain", "ssml"]


@dataclass(frozen=True)
class Voice:
    """A voice available in the Edge TTS engine."""

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

    @property
    def search_text(self) -> str:
        """Lowercase text used to filter voices from the search box."""
        return f"{self.name} {self.locale} {self.gender}".lower()


@dataclass(frozen=True)
class TTSRequest:
    """A synthesis request from the UI."""

    text: str
    text_mode: TextMode
    voice_id: str
    rate: int
    volume: int
    pitch: int = 0


_BREAK_RE = re.compile(r"<break\s+time=[\"']?(\d+)\s*(ms|s)[\"']?\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def ssml_to_plain_text(ssml: str) -> str:
    """Best-effort conversion of a small SSML subset into plain narration text.

    Edge voices have no native SSML support, unlike Windows SAPI voices, so
    arbitrary markup cannot be interpreted with exact timing. ``<break>`` tags
    are approximated with punctuation-based pauses and every other tag is
    stripped while keeping its inner text, so writing SSML for an Edge voice
    never fails, it just loses precise timing control.
    """

    def _replace_break(match: "re.Match[str]") -> str:
        value = int(match.group(1))
        unit = match.group(2).lower()
        seconds = value / 1000 if unit == "ms" else value
        pause_units = max(1, min(6, round(seconds / 0.3)))
        return ", " * pause_units

    text = _BREAK_RE.sub(_replace_break, ssml)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
                id=str(row.get("ShortName", "")),
                name=str(row.get("FriendlyName") or row.get("ShortName", "")),
                locale=str(row.get("Locale", "")),
                gender=str(row.get("Gender", "")),
                description=str(row.get("ShortName", "")),
            )
            for row in rows
        ]

    def synthesize(self, request: TTSRequest, output_path: Path) -> Path:
        if not self.is_available():
            raise RuntimeError("edge-tts is not installed.")

        text = request.text
        if request.text_mode == "ssml":
            text = ssml_to_plain_text(text)
        if not text.strip():
            raise ValueError("The text to synthesize is empty.")

        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        asyncio.run(self._synthesize(replace(request, text=text), output_path))
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
        self.engine = EdgeTTSEngine()

    def list_voices(self) -> list[Voice]:
        return self.engine.list_voices()

    def synthesize(self, request: TTSRequest, *, filename: str | None = None) -> Path:
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            filename = f"tts-{timestamp}.mp3"
        output_path = self.output_dir / filename
        return self.engine.synthesize(request, output_path)
