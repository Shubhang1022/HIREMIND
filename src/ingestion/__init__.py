"""Generic dataset ingestion engine for HireMind AI."""

from src.ingestion.engine import IngestionEngine
from src.ingestion.types import NormalizedCandidate, ParsedRecord

__all__ = ["IngestionEngine", "NormalizedCandidate", "ParsedRecord"]
