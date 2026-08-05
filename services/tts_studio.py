"""Edge TTS engine for the Streamlit studio."""

from __future__ import annotations

import asyncio
import base64
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Union


TextMode = Literal["plain", "ssml"]


class TTSCancelled(Exception):
    """Raised when a synthesis job is cancelled while it is running."""


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


# ---------------------------------------------------------------------------
# SSML: a small, real subset (<break>, <prosody rate="...">) is honoured with
# exact pause durations and per-segment rate, since Edge has no native SSML
# engine. Anything else is stripped. If the input is not well-formed XML, we
# fall back to a rough plain-text approximation rather than failing outright.
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")

_RATE_KEYWORDS = {"x-slow": -50, "slow": -25, "medium": 0, "fast": 25, "x-fast": 50}


def _parse_break_duration_ms(value: str | None) -> int:
    if not value:
        return 0
    value = value.strip().lower()
    try:
        if value.endswith("ms"):
            return round(float(value[:-2]))
        if value.endswith("s"):
            return round(float(value[:-1]) * 1000)
    except ValueError:
        return 0
    return 0


def _parse_rate_attr(value: str | None) -> int:
    """Map an SSML ``rate`` attribute to an Edge-style percentage delta."""
    if not value:
        return 0
    value = value.strip()
    if value.lower() in _RATE_KEYWORDS:
        return _RATE_KEYWORDS[value.lower()]
    if value.endswith("%"):
        try:
            amount = float(value[:-1])
        except ValueError:
            return 0
        # "+20%"/"-10%" are already a delta; a bare "80%" means 80% of normal speed.
        if value.startswith(("+", "-")):
            return round(amount)
        return round(amount - 100)
    return 0


def ssml_to_plain_text(ssml: str) -> str:
    """Best-effort, tag-stripping fallback used only when SSML is not well-formed XML.

    ``<break>`` durations are approximated with punctuation pauses (there is no
    way to insert real silence once we cannot parse the markup), and any other
    tag is removed while keeping its inner text.
    """

    def _replace_break(match: "re.Match[str]") -> str:
        seconds = _parse_break_duration_ms(f"{match.group(1)}{match.group(2)}") / 1000
        pause_units = max(1, min(6, round(seconds / 0.3)))
        return ", " * pause_units

    break_re = re.compile(r"<break\s+time=[\"']?([\d.]+)\s*(ms|s)[\"']?\s*/?>", re.IGNORECASE)
    text = break_re.sub(_replace_break, ssml)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass(frozen=True)
class TextSegment:
    """A chunk of narration text, optionally with a local rate adjustment."""

    text: str
    rate_delta: int = 0


@dataclass(frozen=True)
class BreakSegment:
    """A silent pause of an exact duration."""

    duration_ms: int


Segment = Union[TextSegment, BreakSegment]


def parse_ssml_segments(ssml: str) -> list[Segment]:
    """Parse ``<break>`` and ``<prosody rate="...">`` into an ordered segment list.

    Falls back to a single approximated text segment if the input is not
    well-formed XML (arbitrary pasted SSML sometimes is not).
    """
    ssml = ssml.lstrip("﻿")  # strip a leading UTF-8 BOM from pasted/uploaded files
    try:
        root = ET.fromstring(f"<root>{ssml}</root>")
    except ET.ParseError:
        return [TextSegment(text=ssml_to_plain_text(ssml))]

    segments: list[Segment] = []

    def _walk(element: ET.Element, rate_delta: int) -> None:
        tag = element.tag.split("}")[-1].lower()
        local_rate = rate_delta
        if tag == "prosody":
            local_rate = rate_delta + _parse_rate_attr(element.attrib.get("rate"))

        if element.text and element.text.strip():
            segments.append(TextSegment(text=element.text.strip(), rate_delta=local_rate))

        for child in element:
            child_tag = child.tag.split("}")[-1].lower()
            if child_tag == "break":
                duration_ms = _parse_break_duration_ms(child.attrib.get("time"))
                if duration_ms > 0:
                    segments.append(BreakSegment(duration_ms=duration_ms))
            else:
                _walk(child, local_rate)

            if child.tail and child.tail.strip():
                segments.append(TextSegment(text=child.tail.strip(), rate_delta=rate_delta))

    _walk(root, 0)

    if not segments:
        return [TextSegment(text=ssml_to_plain_text(ssml))]
    return segments


# A single steady-state silent MPEG-2 Layer III frame: 24 kHz mono, 48 kbps
# CBR (Edge's own output format), 144 bytes, covering exactly 24 ms of audio.
# Repeating it N times produces byte-exact silence of any duration with no
# external tool (ffmpeg, etc.) needed at runtime.
_SILENCE_FRAME = base64.b64decode(
    "//NkxHwAAANIAAAAAFVVVVVVVVVMQU1FMy4xMDBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
)
_SILENCE_FRAME_MS = 24


def _silence_bytes(duration_ms: int) -> bytes:
    if duration_ms <= 0:
        return b""
    frames_needed = max(1, round(duration_ms / _SILENCE_FRAME_MS))
    return _SILENCE_FRAME * frames_needed


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

    def synthesize(
        self,
        request: TTSRequest,
        output_path: Path,
        *,
        on_progress: Callable[[float], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Path:
        if not self.is_available():
            raise RuntimeError("edge-tts is not installed.")
        if not request.text.strip():
            raise ValueError("The text to synthesize is empty.")

        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if request.text_mode == "ssml":
            asyncio.run(self._synthesize_ssml(request, output_path, on_progress, should_cancel))
        else:
            asyncio.run(self._synthesize_plain(request, output_path, on_progress, should_cancel))
        return output_path

    async def _synthesize_plain(
        self,
        request: TTSRequest,
        output_path: Path,
        on_progress: Callable[[float], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(
            request.text,
            request.voice_id,
            rate=f"{request.rate:+d}%",
            volume=f"{request.volume:+d}%",
            pitch=f"{request.pitch:+d}Hz",
        )

        total_chars = max(1, len(request.text))
        chars_done = 0

        try:
            with open(output_path, "wb") as audio_file:
                async for chunk in communicate.stream():
                    if should_cancel is not None and should_cancel():
                        raise TTSCancelled("Synthesis cancelled by the user.")

                    if chunk["type"] == "audio":
                        audio_file.write(chunk["data"])
                    elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                        chars_done += len(chunk.get("text", ""))
                        if on_progress is not None:
                            on_progress(min(0.97, chars_done / total_chars))
        except TTSCancelled:
            output_path.unlink(missing_ok=True)
            raise

        if on_progress is not None:
            on_progress(1.0)

    async def _synthesize_ssml(
        self,
        request: TTSRequest,
        output_path: Path,
        on_progress: Callable[[float], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        import edge_tts

        segments = parse_ssml_segments(request.text)
        total = max(1, len(segments))

        try:
            with open(output_path, "wb") as audio_file:
                for index, segment in enumerate(segments):
                    if should_cancel is not None and should_cancel():
                        raise TTSCancelled("Synthesis cancelled by the user.")

                    if isinstance(segment, BreakSegment):
                        audio_file.write(_silence_bytes(segment.duration_ms))
                    else:
                        effective_rate = max(-100, min(100, request.rate + segment.rate_delta))
                        communicate = edge_tts.Communicate(
                            segment.text,
                            request.voice_id,
                            rate=f"{effective_rate:+d}%",
                            volume=f"{request.volume:+d}%",
                            pitch=f"{request.pitch:+d}Hz",
                        )
                        async for chunk in communicate.stream():
                            if should_cancel is not None and should_cancel():
                                raise TTSCancelled("Synthesis cancelled by the user.")
                            if chunk["type"] == "audio":
                                audio_file.write(chunk["data"])

                    if on_progress is not None:
                        on_progress(min(0.97, (index + 1) / total))
        except TTSCancelled:
            output_path.unlink(missing_ok=True)
            raise

        if on_progress is not None:
            on_progress(1.0)


class TTSStudio:
    """Facade used by the UI for voice discovery and synthesis."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        self.engine = EdgeTTSEngine()

    def list_voices(self) -> list[Voice]:
        return self.engine.list_voices()

    def synthesize(
        self,
        request: TTSRequest,
        *,
        filename: str | None = None,
        on_progress: Callable[[float], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Path:
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            filename = f"tts-{timestamp}.mp3"
        output_path = self.output_dir / filename
        return self.engine.synthesize(
            request, output_path, on_progress=on_progress, should_cancel=should_cancel
        )
