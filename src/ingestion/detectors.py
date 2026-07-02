"""File type detection for ingestion."""

from __future__ import annotations

import mimetypes
from pathlib import Path

SUPPORTED_EXTENSIONS = {
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".json": "json",
    ".jsonl": "jsonl",
    ".txt": "txt",
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
}


def detect_file_type(filename: str, content: bytes | None = None) -> str:
    """Detect file type from extension and optional magic bytes."""
    ext = Path(filename).suffix.lower()
    if ext in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[ext]

    if content:
        if content[:4] == b"%PDF":
            return "pdf"
        if content[:2] == b"PK":
            return "xlsx" if ext in (".xlsx", ".docx") else "xlsx"
        if content[:1] in (b"{", b"["):
            return "json"

    guessed, _ = mimetypes.guess_type(filename)
    if guessed == "application/pdf":
        return "pdf"
    if guessed == "text/csv":
        return "csv"
    if guessed == "application/json":
        return "json"

    return "txt"
