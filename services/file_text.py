"""Extract plain text from uploaded files (.txt, .docx, .pdf)."""

from __future__ import annotations

import io

SUPPORTED_EXTENSIONS = ("txt", "docx", "pdf")


class UnsupportedFileError(Exception):
    """Raised when an uploaded file's extension isn't supported."""


def extract_text(filename: str, data: bytes) -> str:
    """Extract plain text from raw file bytes, dispatching by extension.

    Raises ``UnsupportedFileError`` for anything other than .txt/.docx/.pdf.
    """
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if extension == "txt":
        return _extract_txt(data)
    if extension == "docx":
        return _extract_docx(data)
    if extension == "pdf":
        return _extract_pdf(data)
    raise UnsupportedFileError(f"Format de fichier non pris en charge : .{extension or '?'}")


def _extract_txt(data: bytes) -> str:
    # utf-8-sig transparently strips a leading BOM if present.
    return data.decode("utf-8-sig", errors="replace").strip()


def _extract_docx(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs).strip()


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p).strip()
