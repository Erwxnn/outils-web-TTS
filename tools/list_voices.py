"""Dump the exact Edge voice ShortNames usable in <voice name="..."> tags.

Run it from the project root, with the project's virtualenv active:

    python tools/list_voices.py               # all locales, printed
    python tools/list_voices.py fr en         # only locales starting with fr/en
    python tools/list_voices.py fr en -o voices.md

The ``name`` attribute of a ``<voice>`` tag must match the ShortName column
exactly (it is case-sensitive). An internet connection is required: the list
is fetched from the Microsoft Edge speech service.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def _fetch() -> list[dict]:
    import edge_tts

    return await edge_tts.list_voices()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List Edge TTS voices and their ShortName (the value for <voice name=\"...\">)."
    )
    parser.add_argument(
        "prefixes",
        nargs="*",
        help="Optional locale prefixes to keep, e.g. 'fr en'. Default: all locales.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write a Markdown table to this file instead of printing a plain list.",
    )
    args = parser.parse_args()

    try:
        rows = asyncio.run(_fetch())
    except Exception as exc:  # noqa: BLE001 - this is a CLI, report and exit
        print(f"Impossible de recuperer la liste des voix : {exc}", file=sys.stderr)
        print("Verifie ta connexion Internet et que edge-tts est installe.", file=sys.stderr)
        return 1

    if args.prefixes:
        rows = [r for r in rows if any(r.get("Locale", "").startswith(p) for p in args.prefixes)]

    rows.sort(key=lambda r: r.get("ShortName", ""))

    if not rows:
        print("Aucune voix ne correspond a ces prefixes.", file=sys.stderr)
        return 1

    if args.output:
        lines = [
            "# Voix Edge disponibles",
            "",
            "La colonne `ShortName` est la valeur exacte a utiliser dans "
            '`<voice name="...">` (sensible a la casse).',
            "",
            "| ShortName | Locale | Genre |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| `{r.get('ShortName', '')}` | {r.get('Locale', '')} | {r.get('Gender', '')} |"
            for r in rows
        ]
        args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{len(rows)} voix ecrites dans {args.output}")
    else:
        width = max(len(r.get("ShortName", "")) for r in rows)
        for r in rows:
            short = r.get("ShortName", "").ljust(width)
            print(f"{short}  {r.get('Locale', ''):<8} {r.get('Gender', '')}")
        print(f"\n{len(rows)} voix.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
