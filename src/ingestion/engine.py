"""Main ingestion engine — parse any file format and normalize candidates."""

from __future__ import annotations

from pathlib import Path

from src.ingestion.detectors import detect_file_type
from src.ingestion.normalizer import RecordNormalizer
from src.ingestion.parsers import PARSERS
from src.ingestion.types import NormalizedCandidate, ParsedRecord


class IngestionEngine:
    """Detect, parse, infer schema, and normalize candidate data from any file."""

    def __init__(self):
        self.normalizer = RecordNormalizer()

    def parse_file(self, content: bytes, filename: str) -> list[ParsedRecord]:
        file_type = detect_file_type(filename, content)
        parser = PARSERS.get(file_type)
        if not parser:
            raise ValueError(f"Unsupported file type: {file_type} ({filename})")
        return parser.parse(content, filename)

    def parse_folder(self, files: list[tuple[str, bytes]]) -> list[ParsedRecord]:
        """Parse multiple files (folder upload). Each resume/doc = one candidate."""
        all_records: list[ParsedRecord] = []
        for filename, content in files:
            try:
                records = self.parse_file(content, filename)
                all_records.extend(records)
            except Exception:
                continue
        return all_records

    def normalize_records(self, records: list[ParsedRecord]) -> list[NormalizedCandidate]:
        if not records:
            return []

        # Infer schema from first tabular record
        tabular = [r for r in records if len(r.raw) > 2]
        if tabular:
            self.normalizer.infer_and_set_schema(list(tabular[0].raw.keys()))

        return [self.normalizer.normalize(r) for r in records]

    def ingest(self, content: bytes, filename: str) -> tuple[list[NormalizedCandidate], dict]:
        """Full pipeline: parse → normalize. Returns candidates + metadata."""
        records = self.parse_file(content, filename)
        candidates = self.normalize_records(records)
        metadata = {
            "filename": filename,
            "file_type": detect_file_type(filename, content),
            "records_parsed": len(records),
            "candidates_normalized": len(candidates),
            "schema_mapping": self.normalizer.schema_mapping,
        }
        return candidates, metadata

    def ingest_folder(self, files: list[tuple[str, bytes]]) -> tuple[list[NormalizedCandidate], dict]:
        records = self.parse_folder(files)
        candidates = self.normalize_records(records)
        metadata = {
            "files_processed": len(files),
            "records_parsed": len(records),
            "candidates_normalized": len(candidates),
            "schema_mapping": self.normalizer.schema_mapping,
        }
        return candidates, metadata

    def parse_job_description(self, content: bytes, filename: str) -> dict:
        """Extract job description text from uploaded file."""
        file_type = detect_file_type(filename, content)
        if file_type in ("txt",):
            return {"title": Path(filename).stem, "description": content.decode("utf-8", errors="replace")}
        if file_type in ("json", "jsonl"):
            records = self.parse_file(content, filename)
            if records:
                raw = records[0].raw
                return {
                    "title": raw.get("title") or raw.get("job_title") or Path(filename).stem,
                    "description": raw.get("description") or raw.get("job_description") or str(raw),
                    "company": raw.get("company"),
                    "location": raw.get("location"),
                    "required_skills": raw.get("required_skills") or raw.get("skills") or [],
                }
        if file_type in ("pdf", "docx"):
            records = self.parse_file(content, filename)
            text = records[0].text_content if records else ""
            return {"title": Path(filename).stem, "description": text}
        # CSV/XLSX single row JD
        records = self.parse_file(content, filename)
        if records:
            raw = records[0].raw
            desc_parts = [str(v) for v in raw.values() if v]
            return {
                "title": raw.get("title") or raw.get("job_title") or Path(filename).stem,
                "description": "\n".join(desc_parts),
            }
        return {"title": Path(filename).stem, "description": ""}
