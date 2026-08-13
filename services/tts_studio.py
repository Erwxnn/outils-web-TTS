"""Edge TTS engine for the Streamlit studio."""

from __future__ import annotations

import asyncio
import base64
import difflib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Literal, Union


TextMode = Literal["plain", "ssml"]


class TTSCancelled(Exception):
    """Raised when a synthesis job is cancelled while it is running."""


class VoiceValidationError(Exception):
    """Raised when a voice name in the request is malformed or unknown.

    Checked *before* any network call, so a typo in a ``<voice name="...">``
    fails immediately with an actionable message instead of aborting halfway
    through a synthesis job with a raw websocket error.
    """


# Parsed out of Edge's ``FriendlyName``, which looks like
# "Microsoft Ava Online (Natural) - English (United States)". The language and
# country names come straight from Microsoft this way, so no locale database
# (nor extra dependency) is needed. Anything that does not match falls back to
# the raw locale subtags.
_FRIENDLY_TAIL_RE = re.compile(r"^([^()]+?)\s*\(([^()]+)\)$")


def parse_friendly_name(friendly_name: str) -> tuple[str, str]:
    """Extract ``(language, country)`` from an Edge ``FriendlyName``.

    Returns ``("", "")`` when the name does not follow the expected shape.
    """
    if " - " not in friendly_name:
        return "", ""
    tail = friendly_name.rsplit(" - ", 1)[-1].strip()
    match = _FRIENDLY_TAIL_RE.match(tail)
    if match is None:
        return "", ""
    return match.group(1).strip(), match.group(2).strip()


@dataclass(frozen=True)
class Voice:
    """A voice available in the Edge TTS engine."""

    id: str
    name: str
    locale: str
    gender: str = ""
    description: str = ""
    language: str = ""
    country: str = ""

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.locale:
            parts.append(self.locale)
        if self.gender:
            parts.append(self.gender)
        # The ShortName is the exact value a <voice name="..."> tag expects, so
        # it is shown here to be readable straight off the selector.
        if self.id and self.id not in parts:
            parts.append(self.id)
        return " - ".join(parts)

    @property
    def language_display(self) -> str:
        """Human-readable language, falling back to the locale's language subtag."""
        if self.language:
            return self.language
        return self.locale.split("-")[0] if self.locale else ""

    @property
    def country_display(self) -> str:
        """Human-readable country, falling back to the locale's region subtag."""
        if self.country:
            return self.country
        parts = self.locale.split("-")
        return parts[1] if len(parts) > 1 else ""

    @property
    def short_display(self) -> str:
        """Just the voice's own name, e.g. ``AndrewMultilingual`` for
        ``en-US-AndrewMultilingualNeural`` -- short enough for a browse list."""
        stem = self.id
        prefix = f"{self.locale}-"
        if self.locale and stem.startswith(prefix):
            stem = stem[len(prefix) :]
        else:
            # Fall back to dropping the first two dash-separated subtags.
            bits = stem.split("-")
            if len(bits) > 2:
                stem = "-".join(bits[2:])
        if stem.endswith("Neural"):
            stem = stem[: -len("Neural")]
        return stem or self.id

    @property
    def is_multilingual(self) -> bool:
        """Whether this is one of Edge's ``*MultilingualNeural`` voices."""
        return "multilingual" in self.id.lower()

    @property
    def search_text(self) -> str:
        """Lowercase text used to filter voices from the search box."""
        return (
            f"{self.name} {self.locale} {self.gender} {self.id} "
            f"{self.language} {self.country}"
        ).lower()


def voice_from_row(row: dict[str, Any]) -> Voice:
    """Build a :class:`Voice` from one ``edge_tts.list_voices()`` entry."""
    short_name = str(row.get("ShortName", ""))
    friendly_name = str(row.get("FriendlyName") or short_name)
    language, country = parse_friendly_name(friendly_name)
    return Voice(
        id=short_name,
        name=friendly_name,
        locale=str(row.get("Locale", "")),
        gender=str(row.get("Gender", "")),
        description=short_name,
        language=language,
        country=country,
    )


# ---------------------------------------------------------------------------
# Voice browsing: pure helpers used by the UI's language/country filters.
# ---------------------------------------------------------------------------


def list_languages(voices: Iterable[Voice]) -> list[str]:
    """Sorted, de-duplicated language names present in ``voices``."""
    return sorted({v.language_display for v in voices if v.language_display})


def list_countries(voices: Iterable[Voice], language: str | None = None) -> list[str]:
    """Sorted country names, optionally restricted to a single language."""
    return sorted(
        {
            v.country_display
            for v in voices
            if v.country_display and (not language or v.language_display == language)
        }
    )


def list_genders(voices: Iterable[Voice]) -> list[str]:
    """Sorted gender values present in ``voices``."""
    return sorted({v.gender for v in voices if v.gender})


def filter_voices(
    voices: Iterable[Voice],
    *,
    language: str | None = None,
    country: str | None = None,
    gender: str | None = None,
    multilingual_only: bool = False,
    query: str | None = None,
) -> list[Voice]:
    """Filter voices for the browse panel.

    Every criterion is optional and they combine with AND. ``query`` is matched
    case-insensitively against the voice's searchable text, term by term, so
    "andrew multi" matches ``en-US-AndrewMultilingualNeural``.
    """
    terms = (query or "").lower().split()
    result = []
    for voice in voices:
        if language and voice.language_display != language:
            continue
        if country and voice.country_display != country:
            continue
        if gender and voice.gender != gender:
            continue
        if multilingual_only and not voice.is_multilingual:
            continue
        if terms:
            haystack = voice.search_text
            if not all(term in haystack for term in terms):
                continue
        result.append(voice)
    return result


# ---------------------------------------------------------------------------
# Voice validation
#
# This mirrors edge-tts's own check (``data_classes.TTSConfig``), which only
# validates the *shape* of a voice name -- a well-formed but non-existent name
# such as "fr-FR-JeanNeural" passes it and then fails mid-synthesis with a raw
# websocket error, after part of the output has already been written. Checking
# names against the real voice list up front turns that into one clear message.
# ---------------------------------------------------------------------------

_VOICE_ID_RE = re.compile(r"^[a-z]{2,}-[A-Z]{2,}-.+Neural$")


def collect_request_voice_ids(text: str, text_mode: TextMode, base_voice_id: str) -> list[str]:
    """Every distinct voice a request will actually use, base voice included.

    Order is stable: the base voice first, then ``<voice name="...">``
    overrides in document order.
    """
    ids = [base_voice_id] if base_voice_id else []
    if text_mode == "ssml":
        for segment in parse_ssml_segments(text):
            if isinstance(segment, TextSegment) and segment.voice_id:
                ids.append(segment.voice_id)
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def validate_voice_ids(
    voice_ids: Iterable[str],
    known_voice_ids: Collection[str] | None = None,
) -> None:
    """Raise :class:`VoiceValidationError` on any malformed or unknown voice.

    ``known_voice_ids`` is the list of voices the service actually offers. When
    omitted, only the name's shape is checked (no network access needed).
    """
    malformed: list[str] = []
    unknown: list[tuple[str, list[str]]] = []

    known_lookup = {k.lower(): k for k in known_voice_ids} if known_voice_ids else {}

    for voice_id in voice_ids:
        if _VOICE_ID_RE.match(voice_id) is None:
            malformed.append(voice_id)
        elif known_lookup and voice_id.lower() not in known_lookup:
            close = difflib.get_close_matches(voice_id, list(known_lookup.values()), n=3, cutoff=0.6)
            unknown.append((voice_id, close))

    if not malformed and not unknown:
        return

    problems: list[str] = []
    for voice_id in malformed:
        problems.append(
            f"'{voice_id}' n'a pas un format de nom valide "
            "(attendu : langue-REGION-NomNeural, par exemple fr-FR-HenriNeural)."
        )
    for voice_id, close in unknown:
        message = f"'{voice_id}' ne correspond a aucune voix disponible."
        if close:
            message += " Vouliez-vous dire " + ", ".join(close) + " ?"
        problems.append(message)

    raise VoiceValidationError(" ".join(problems))


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
    """A chunk of narration text, optionally with a local rate adjustment
    and/or a voice override (see ``<voice name="...">`` in ``parse_ssml_segments``)."""

    text: str
    rate_delta: int = 0
    voice_id: str | None = None


@dataclass(frozen=True)
class BreakSegment:
    """A silent pause of an exact duration."""

    duration_ms: int


Segment = Union[TextSegment, BreakSegment]


def parse_ssml_segments(ssml: str) -> list[Segment]:
    """Parse ``<break>``, ``<prosody rate="...">`` and ``<voice name="...">`` into
    an ordered segment list.

    ``<voice name="...">`` lets a segment be spoken with a different Edge voice
    than the one selected in the UI (``name`` is an Edge ``ShortName``, e.g.
    ``fr-FR-HenriNeural``) -- handy for mixed-language text where a French word
    should not be read with the base voice's English phonetics.

    Falls back to a single approximated text segment if the input is not
    well-formed XML (arbitrary pasted SSML sometimes is not).
    """
    ssml = ssml.lstrip("﻿")  # strip a leading UTF-8 BOM from pasted/uploaded files
    try:
        root = ET.fromstring(f"<root>{ssml}</root>")
    except ET.ParseError:
        return [TextSegment(text=ssml_to_plain_text(ssml))]

    segments: list[Segment] = []

    def _walk(element: ET.Element, rate_delta: int, voice_id: str | None) -> None:
        tag = element.tag.split("}")[-1].lower()
        local_rate = rate_delta
        local_voice = voice_id
        if tag == "prosody":
            local_rate = rate_delta + _parse_rate_attr(element.attrib.get("rate"))
        elif tag == "voice":
            name = element.attrib.get("name", "").strip()
            if name:
                local_voice = name

        if element.text and element.text.strip():
            segments.append(
                TextSegment(text=element.text.strip(), rate_delta=local_rate, voice_id=local_voice)
            )

        for child in element:
            child_tag = child.tag.split("}")[-1].lower()
            if child_tag == "break":
                duration_ms = _parse_break_duration_ms(child.attrib.get("time"))
                if duration_ms > 0:
                    segments.append(BreakSegment(duration_ms=duration_ms))
            else:
                _walk(child, local_rate, local_voice)

            # ``child.tail`` sits *inside* ``element``, after ``child``, so it
            # inherits this element's own rate/voice -- not the context that was
            # passed in. Using the latter would silently drop the prosody rate or
            # the voice override for any text following a nested tag, e.g. the
            # "Se procurer." in <voice name="fr-FR-..">Obtenir.<break/>Se procurer.</voice>.
            if child.tail and child.tail.strip():
                segments.append(
                    TextSegment(text=child.tail.strip(), rate_delta=local_rate, voice_id=local_voice)
                )

    _walk(root, 0, None)

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
        return [voice_from_row(row) for row in rows]

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
                            segment.voice_id or request.voice_id,
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
        known_voice_ids: Collection[str] | None = None,
    ) -> Path:
        # Validate before anything is opened or sent: a bad <voice name="...">
        # should not leave a truncated file behind.
        validate_voice_ids(
            collect_request_voice_ids(request.text, request.text_mode, request.voice_id),
            known_voice_ids,
        )
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            filename = f"tts-{timestamp}.mp3"
        output_path = self.output_dir / filename
        return self.engine.synthesize(
            request, output_path, on_progress=on_progress, should_cancel=should_cancel
        )
