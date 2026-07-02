import time
from typing import Any, Dict, Optional
from app.core.config import settings

class CacheService:
    # Key format: f"{bucket_id}:{path}"
    # Value format: (value, expires_at)
    _in_memory_cache: Dict[str, tuple[Any, float]] = {}
    _active_project_id: Optional[str] = None

    @classmethod
    def get(cls, bucket_id: str, path: str) -> Optional[Any]:
        if not settings.use_faiss_cache:
            return None
        key = f"{bucket_id}:{path}"
        if key in cls._in_memory_cache:
            value, expires_at = cls._in_memory_cache[key]
            if time.time() < expires_at:
                return value
            else:
                del cls._in_memory_cache[key]
        return None

    @classmethod
    def set(cls, bucket_id: str, path: str, value: Any, ttl: float = 600.0) -> None:
        if not settings.use_faiss_cache:
            return
        
        # Enforce single-active-project caching
        # Path format: project_id/...
        parts = path.split("/", 1)
        if len(parts) > 1:
            project_id = parts[0]
            if cls._active_project_id is not None and cls._active_project_id != project_id:
                # Clear all cache from the previous project to avoid multiple projects in memory
                cls.clear()
            cls._active_project_id = project_id
            
        key = f"{bucket_id}:{path}"
        cls._in_memory_cache[key] = (value, time.time() + ttl)

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
        if cls._active_project_id == project_id:
            cls._active_project_id = None

    @classmethod
    def clear(cls) -> None:
        cls._in_memory_cache.clear()
        cls._active_project_id = None
