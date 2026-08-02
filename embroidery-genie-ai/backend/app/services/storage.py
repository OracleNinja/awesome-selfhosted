"""Object storage.

Two backends behind one interface:

* **supabase** — Supabase Storage over its REST API, with signed URLs.
* **local** — the filesystem, for development and for self-hosted installs that
  do not want a cloud dependency.

Paths are always ``{org_id}/{design_id}/{kind}/{filename}`` so a tenant's
objects are contiguous and a delete is a prefix operation.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, fallback: str = "file") -> str:
    cleaned = _SAFE.sub("_", (name or "").strip()).strip("._")
    return cleaned[:120] or fallback


def build_path(org_id: str, design_id: str, kind: str, filename: str) -> str:
    return f"{org_id}/{design_id}/{kind}/{safe_filename(filename)}"


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StorageBackend(ABC):
    @abstractmethod
    def put(self, path: str, data: bytes, content_type: str | None = None) -> str: ...

    @abstractmethod
    def get(self, path: str) -> bytes: ...

    @abstractmethod
    def delete(self, path: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    @abstractmethod
    def url(self, path: str, ttl: int | None = None) -> str: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...


class LocalStorage(StorageBackend):
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        # Refuse anything that escapes the storage root.
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"Refusing to access path outside storage root: {path}")
        return target

    def put(self, path: str, data: bytes, content_type: str | None = None) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    def get(self, path: str) -> bytes:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        return target.read_bytes()

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.exists():
            target.unlink()

    def delete_prefix(self, prefix: str) -> None:
        target = self._resolve(prefix)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)

    def url(self, path: str, ttl: int | None = None) -> str:
        # Served back through the API so access control still applies.
        return f"{settings.api_v1_prefix}/files/raw/{path}"

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()


class SupabaseStorage(StorageBackend):
    def __init__(self, url: str, service_key: str, bucket: str):
        if not url or not service_key:
            raise ValueError(
                "Supabase storage requires SUPABASE_URL and SUPABASE_SERVICE_KEY."
            )
        self.base = url.rstrip("/")
        self.bucket = bucket
        self.headers = {
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
        }

    def _object_url(self, path: str) -> str:
        return f"{self.base}/storage/v1/object/{self.bucket}/{path}"

    def put(self, path: str, data: bytes, content_type: str | None = None) -> str:
        headers = dict(self.headers)
        headers["Content-Type"] = content_type or (
            mimetypes.guess_type(path)[0] or "application/octet-stream"
        )
        headers["x-upsert"] = "true"
        response = httpx.post(self._object_url(path), content=data, headers=headers, timeout=60)
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase upload failed ({response.status_code}): {response.text}")
        return path

    def get(self, path: str) -> bytes:
        response = httpx.get(self._object_url(path), headers=self.headers, timeout=60)
        if response.status_code == 404:
            raise FileNotFoundError(path)
        response.raise_for_status()
        return response.content

    def delete(self, path: str) -> None:
        httpx.request(
            "DELETE", self._object_url(path), headers=self.headers, timeout=30
        )

    def delete_prefix(self, prefix: str) -> None:
        listing = httpx.post(
            f"{self.base}/storage/v1/object/list/{self.bucket}",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"prefix": prefix, "limit": 1000},
            timeout=30,
        )
        if listing.status_code >= 400:
            return
        names = [f"{prefix}/{item['name']}" for item in listing.json() or []]
        if names:
            httpx.request(
                "DELETE",
                f"{self.base}/storage/v1/object/{self.bucket}",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"prefixes": names},
                timeout=30,
            )

    def url(self, path: str, ttl: int | None = None) -> str:
        expires = ttl or settings.signed_url_ttl_seconds
        response = httpx.post(
            f"{self.base}/storage/v1/object/sign/{self.bucket}/{path}",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"expiresIn": expires},
            timeout=30,
        )
        if response.status_code >= 400:
            log.warning("Could not sign %s: %s", path, response.text)
            return ""
        signed = response.json().get("signedURL", "")
        return f"{self.base}/storage/v1{signed}" if signed.startswith("/") else signed

    def exists(self, path: str) -> bool:
        response = httpx.head(self._object_url(path), headers=self.headers, timeout=20)
        return response.status_code < 400


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        if settings.storage_backend == "supabase":
            _backend = SupabaseStorage(
                settings.supabase_url, settings.supabase_service_key,
                settings.supabase_storage_bucket,
            )
        else:
            _backend = LocalStorage(settings.local_storage_path)
        log.info("Storage backend: %s", type(_backend).__name__)
    return _backend


def reset_storage() -> None:
    """Test hook."""
    global _backend
    _backend = None
