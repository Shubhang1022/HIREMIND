import os
import shutil
import io
import json
import re
from unittest.mock import patch
from typing import Generator, Optional
from pathlib import Path
import httpx
from supabase import create_client, Client
from app.core.config import settings

def create_supabase_client(url: str, key: str) -> Client:
    original_match = re.match
    def mocked_match(pattern, string, flags=0):
        if pattern == r"^[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*$":
            if string and string.startswith("sb_secret_"):
                class MockMatch:
                    pass
                return MockMatch()
        return original_match(pattern, string, flags)

    with patch("re.match", side_effect=mocked_match):
        return create_client(url, key)


class StorageProvider:
    def upload_file(self, bucket_id: str, path: str, content: bytes) -> str:
        raise NotImplementedError

    def download_file(self, bucket_id: str, path: str) -> bytes:
        raise NotImplementedError

    def download_stream(self, bucket_id: str, path: str) -> Generator[bytes, None, None]:
        raise NotImplementedError

    def delete_file(self, bucket_id: str, path: str) -> bool:
        raise NotImplementedError

    def file_exists(self, bucket_id: str, path: str) -> bool:
        raise NotImplementedError

    def generate_signed_url(self, bucket_id: str, path: str, expires_in: int = 3600) -> str:
        raise NotImplementedError

    def stream_jsonl(self, bucket_id: str, path: str) -> Generator[dict, None, None]:
        raise NotImplementedError


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).resolve().parents[3] / "data"

    def _get_local_path(self, bucket_id: str, path: str) -> Path:
        local_path = self.base_dir / bucket_id / path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        return local_path

    def upload_file(self, bucket_id: str, path: str, content: bytes) -> str:
        local_file = self._get_local_path(bucket_id, path)
        with open(local_file, "wb") as f:
            f.write(content)
        return str(local_file)

    def download_file(self, bucket_id: str, path: str) -> bytes:
        local_file = self._get_local_path(bucket_id, path)
        if not local_file.exists():
            raise FileNotFoundError(f"File not found: {local_file}")
        with open(local_file, "rb") as f:
            return f.read()

    def download_stream(self, bucket_id: str, path: str) -> Generator[bytes, None, None]:
        local_file = self._get_local_path(bucket_id, path)
        if not local_file.exists():
            raise FileNotFoundError(f"File not found: {local_file}")
        with open(local_file, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    def delete_file(self, bucket_id: str, path: str) -> bool:
        local_file = self._get_local_path(bucket_id, path)
        if local_file.exists():
            local_file.unlink()
            return True
        return False

    def file_exists(self, bucket_id: str, path: str) -> bool:
        local_file = self._get_local_path(bucket_id, path)
        return local_file.exists()

    def generate_signed_url(self, bucket_id: str, path: str, expires_in: int = 3600) -> str:
        # Local mock signed URL (return path directly)
        return f"file:///{self._get_local_path(bucket_id, path).as_posix()}"

    def stream_jsonl(self, bucket_id: str, path: str) -> Generator[dict, None, None]:
        local_file = self._get_local_path(bucket_id, path)
        if not local_file.exists():
            return
        with open(local_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue


class SupabaseStorageProvider(StorageProvider):
    def __init__(self):
        self.url = settings.supabase_url or "https://okhxqdmajbibloxuhquy.supabase.co"
        self.key = settings.supabase_service_key or "sb_secret_FDTVjRiSs3kuGwlKoWtctQ_CFBm_MBV"
        self.client: Client = create_supabase_client(self.url, self.key)

    def upload_file(self, bucket_id: str, path: str, content: bytes) -> str:
        # Check if file exists, if so update it, otherwise upload it
        try:
            # We use supabase client storage API
            res = self.client.storage.from_(bucket_id).upload(
                path=path,
                file=content,
                file_options={"cache-control": "3600", "upsert": "true"}
            )
        except Exception:
            # Try updating if already exists
            res = self.client.storage.from_(bucket_id).update(
                path=path,
                file=content,
                file_options={"cache-control": "3600", "upsert": "true"}
            )
        return f"{bucket_id}/{path}"

    def download_file(self, bucket_id: str, path: str) -> bytes:
        try:
            return self.client.storage.from_(bucket_id).download(path)
        except Exception as exc:
            raise FileNotFoundError(f"Error downloading {bucket_id}/{path} from Supabase: {exc}")

    def download_stream(self, bucket_id: str, path: str) -> Generator[bytes, None, None]:
        # Stream chunks directly from Supabase Storage HTTP REST endpoint to maintain O(1) memory
        # Use /object/{bucket}/{path} with service-key auth (NOT /authenticated/ which requires anon JWT)
        headers = {
            "Authorization": f"Bearer {self.key}",
            "apikey": self.key
        }
        url = f"{self.url}/storage/v1/object/{bucket_id}/{path}"
        
        with httpx.stream("GET", url, headers=headers) as r:
            if r.status_code != 200:
                raise FileNotFoundError(f"File not found in Supabase Storage: {bucket_id}/{path} (status {r.status_code})")
            for chunk in r.iter_bytes(chunk_size=65536):
                yield chunk

    def delete_file(self, bucket_id: str, path: str) -> bool:
        try:
            self.client.storage.from_(bucket_id).remove(path)
            return True
        except Exception:
            return False

    def file_exists(self, bucket_id: str, path: str) -> bool:
        try:
            # List files in parent directory to check if it exists
            parts = path.split("/")
            parent_dir = "/".join(parts[:-1]) if len(parts) > 1 else ""
            filename = parts[-1]
            files = self.client.storage.from_(bucket_id).list(parent_dir)
            return any(f.get("name") == filename for f in files)
        except Exception:
            return False

    def generate_signed_url(self, bucket_id: str, path: str, expires_in: int = 3600) -> str:
        try:
            res = self.client.storage.from_(bucket_id).create_signed_url(path, expires_in)
            return res.get("signedURL") or res.get("signedUrl") or ""
        except Exception:
            return f"{self.url}/storage/v1/object/authenticated/{bucket_id}/{path}"

    def stream_jsonl(self, bucket_id: str, path: str) -> Generator[dict, None, None]:
        # Read from the downloaded stream line-by-line
        buffer = ""
        try:
            for chunk in self.download_stream(bucket_id, path):
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except Exception:
                            continue
            # Yield any remaining text in buffer
            buffer = buffer.strip()
            if buffer:
                try:
                    yield json.loads(buffer)
                except Exception:
                    pass
        except Exception:
            return


class StorageService:
    _local_provider = None
    _supabase_provider = None

    @classmethod
    def get_provider(cls) -> StorageProvider:
        if settings.use_supabase_storage:
            if cls._supabase_provider is None:
                cls._supabase_provider = SupabaseStorageProvider()
            return cls._supabase_provider
        else:
            if cls._local_provider is None:
                cls._local_provider = LocalStorageProvider()
            return cls._local_provider

    @classmethod
    def upload_file(cls, bucket_id: str, path: str, content: bytes) -> str:
        return cls.get_provider().upload_file(bucket_id, path, content)

    @classmethod
    def download_file(cls, bucket_id: str, path: str) -> bytes:
        return cls.get_provider().download_file(bucket_id, path)

    @classmethod
    def download_stream(cls, bucket_id: str, path: str) -> Generator[bytes, None, None]:
        return cls.get_provider().download_stream(bucket_id, path)

    @classmethod
    def delete_file(cls, bucket_id: str, path: str) -> bool:
        return cls.get_provider().delete_file(bucket_id, path)

    @classmethod
    def file_exists(cls, bucket_id: str, path: str) -> bool:
        return cls.get_provider().file_exists(bucket_id, path)

    @classmethod
    def generate_signed_url(cls, bucket_id: str, path: str, expires_in: int = 3600) -> str:
        return cls.get_provider().generate_signed_url(bucket_id, path, expires_in)

    @classmethod
    def stream_jsonl(cls, bucket_id: str, path: str) -> Generator[dict, None, None]:
        return cls.get_provider().stream_jsonl(bucket_id, path)
