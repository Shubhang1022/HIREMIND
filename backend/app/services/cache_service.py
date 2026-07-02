from typing import Any, Dict, Optional
from app.core.config import settings

class CacheService:
    # Key format: f"{bucket_id}:{path}"
    _in_memory_cache: Dict[str, Any] = {}

    @classmethod
    def get(cls, bucket_id: str, path: str) -> Optional[Any]:
        if not settings.use_faiss_cache:
            return None
        key = f"{bucket_id}:{path}"
        return cls._in_memory_cache.get(key)

    @classmethod
    def set(cls, bucket_id: str, path: str, value: Any) -> None:
        if not settings.use_faiss_cache:
            return
        key = f"{bucket_id}:{path}"
        cls._in_memory_cache[key] = value

    @classmethod
    def invalidate(cls, bucket_id: str, path: str) -> None:
        key = f"{bucket_id}:{path}"
        if key in cls._in_memory_cache:
            del cls._in_memory_cache[key]

    @classmethod
    def invalidate_project(cls, project_id: str) -> None:
        # Invalidate all keys associated with a project ID
        prefix = f"{project_id}/"
        keys_to_del = [
            k for k in cls._in_memory_cache.keys()
            if k.split(":", 1)[-1].startswith(prefix)
        ]
        for k in keys_to_del:
            del cls._in_memory_cache[k]

    @classmethod
    def clear(cls) -> None:
        cls._in_memory_cache.clear()
