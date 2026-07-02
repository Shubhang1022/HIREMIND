"""File parsers for supported formats."""

from __future__ import annotations

import csv
import io
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.ingestion.types import ParsedRecord


class BaseParser(ABC):
    @abstractmethod
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        ...


class CSVParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        records: list[ParsedRecord] = []
        for i, row in enumerate(reader):
            records.append(ParsedRecord(
                source_file=filename,
                record_index=i,
                raw=dict(row),
            ))
        return records


class JSONParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        text = content.decode("utf-8", errors="replace")
        data = json.loads(text)
        records: list[ParsedRecord] = []

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Find first list value (candidates array)
            items = None
            for key in ("candidates", "data", "records", "items", "results"):
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            if items is None:
                items = [data]
        else:
            items = [data]

        for i, item in enumerate(items):
            if isinstance(item, dict):
                records.append(ParsedRecord(
                    source_file=filename,
                    record_index=i,
                    raw=item,
                ))
        return records


class JSONLParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        text = content.decode("utf-8", errors="replace")
        records: list[ParsedRecord] = []
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(ParsedRecord(
                        source_file=filename,
                        record_index=i,
                        raw=item,
                    ))
            except json.JSONDecodeError:
                continue
        return records


class TXTParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        text = content.decode("utf-8", errors="replace")
        # Single document = single candidate
        return [ParsedRecord(
            source_file=filename,
            record_index=0,
            raw={"content": text, "filename": Path(filename).stem},
            text_content=text,
        )]


class XLSXParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        try:
            import openpyxl
        except ImportError:
            raise ImportError("openpyxl required for XLSX parsing: pip install openpyxl")

        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
        records: list[ParsedRecord] = []
        for i, row in enumerate(rows[1:], start=0):
            raw = {headers[j]: (str(v) if v is not None else "") for j, v in enumerate(row) if j < len(headers)}
            if any(v for v in raw.values()):
                records.append(ParsedRecord(source_file=filename, record_index=i, raw=raw))
        wb.close()
        return records


class PDFParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        try:
            import pypdf
        except ImportError:
            raise ImportError("pypdf required for PDF parsing: pip install pypdf")

        reader = pypdf.PdfReader(io.BytesIO(content))
        text_parts = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        text = "\n".join(text_parts)
        name = Path(filename).stem.replace("_", " ").replace("-", " ")

        return [ParsedRecord(
            source_file=filename,
            record_index=0,
            raw={"filename": Path(filename).name, "full_name": name},
            text_content=text,
        )]


class DOCXParser(BaseParser):
    def parse(self, content: bytes, filename: str) -> list[ParsedRecord]:
        try:
            import docx
        except ImportError:
            raise ImportError("python-docx required for DOCX parsing: pip install python-docx")

        doc = docx.Document(io.BytesIO(content))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        name = Path(filename).stem.replace("_", " ").replace("-", " ")

        return [ParsedRecord(
            source_file=filename,
            record_index=0,
            raw={"filename": Path(filename).name, "full_name": name},
            text_content=text,
        )]


PARSERS: dict[str, BaseParser] = {
    "csv": CSVParser(),
    "json": JSONParser(),
    "jsonl": JSONLParser(),
    "txt": TXTParser(),
    "xlsx": XLSXParser(),
    "pdf": PDFParser(),
    "docx": DOCXParser(),
}
