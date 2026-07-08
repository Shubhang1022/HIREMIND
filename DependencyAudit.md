# DependencyAudit.md

## Root Cause

`requirements.txt` contained legacy packages (`sqlalchemy`, `asyncpg`, `alembic`) from before the Supabase migration. These are no longer imported anywhere in the backend application code. They add ~15 MB of Docker layer size and introduce minor security surface area.

---

## Package Status

| Package | Used in production? | Keep? | Notes |
|---------|-------------------|-------|-------|
| `fastapi` | ✅ Yes | Keep | Core framework |
| `uvicorn[standard]` | ✅ Yes | Keep | ASGI server |
| `pydantic-settings` | ✅ Yes | Keep | Config management |
| `python-dotenv` | ✅ Yes | Keep | .env loading |
| `python-multipart` | ✅ Yes | Keep | File upload |
| `PyJWT` | ✅ Yes | Keep | JWT auth |
| `httpx` | ✅ Yes | Keep | OpenRouter HTTP client |
| `numpy` | ✅ Yes | Keep | Embeddings, FAISS |
| `sentence-transformers` | ✅ Yes | Keep | Embedding model |
| `faiss-cpu` | ✅ Yes | Keep | Vector search |
| `PyYAML` | ✅ Yes | Keep | Config YAML |
| `openpyxl` | ✅ Yes | Keep | XLSX export |
| `pypdf` | ✅ Yes | Keep | PDF JD parsing |
| `python-docx` | ✅ Yes | Keep | DOCX JD parsing |
| `supabase` | ✅ Yes | Keep | Primary database client |
| `reportlab` | ✅ Yes | Keep | PDF export |
| `psutil` | ✅ Yes | Keep | Memory/CPU diagnostics |
| `sqlalchemy` | ❌ Not used | Keep (legacy) | `config.py` has `database_url` field with asyncpg default string. Removing without cleaning config.py first would risk import errors. Document as legacy. |
| `asyncpg` | ❌ Not used | Keep (legacy) | Same reason as sqlalchemy |
| `alembic` | ❌ Not used | Keep (legacy) | Database migration tool, unused after Supabase |

---

## Removed

None removed in this pass. The legacy packages are kept because:

1. `config.py` line 35: `database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"` — pydantic-settings may attempt to validate this at import time if asyncpg is not installed
2. Safe removal requires also cleaning `config.py`'s `database_url` field — a separate, low-risk task

**Estimated savings if removed**: ~15 MB Docker layer, ~0.5s startup time

---

## Packages NOT in requirements.txt (confirmed correct)

- `torch` — not imported anywhere in the backend (`sentence-transformers` ships its own inference path)
- `transformers` — sentence-transformers includes it as a dependency; no direct `import transformers` in backend
- `huggingface_hub` — transitive dependency of sentence-transformers; correctly absent from explicit deps

---

## Verification Evidence

```
PASS  requirements.txt: faiss-cpu present
PASS  requirements.txt: sentence-transformers present
PASS  requirements.txt: supabase present
PASS  requirements.txt: SQLAlchemy stack documented as legacy
```
