"""Application configuration for the TTS studio."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the TTS studio."""

    output_path: Path
    project_root: Path

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        root = project_root or Path(__file__).resolve().parent

        output_path = Path(os.getenv("OUTPUT_PATH", root / "outputs"))
        if not output_path.is_absolute():
            output_path = root / output_path

        return cls(
            output_path=output_path,
            project_root=root,
        )
