"""Where bill photos are kept, and how they are deleted (privacy promise: 30 days, unless the owner agreed
to keep them for the reading test).

Two interchangeable photo stores with the same methods:
  LocalPhotos     files under data/raw/bills/<shop code>/ on this computer. For the laptop and tests.
  SupabasePhotos  a PRIVATE Supabase Storage bucket. Used when SUPABASE_URL and SUPABASE_SERVICE_KEY are set.
                  Needed once the bot runs on a host: hosting servers wipe their own disk on every restart.

Methods: save(shop_code, data) -> ref   get(ref) -> bytes   delete(ref)   delete_shop(shop_code)
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import httpx

SB_PREFIX = "sb:"


class LocalPhotos:
    def __init__(self, root: Path | str = "data/raw/bills"):
        self.root = Path(root)

    def save(self, shop_code: str, data: bytes) -> str:
        path = self.root / shop_code / f"{uuid.uuid4().hex}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get(self, ref: str) -> bytes:
        return Path(ref).read_bytes()

    def delete(self, ref: str) -> None:
        Path(ref).unlink(missing_ok=True)

    def delete_shop(self, shop_code: str) -> None:
        shutil.rmtree(self.root / shop_code, ignore_errors=True)


class SupabasePhotos:
    def __init__(self, url: str, key: str, bucket: str = "bill-photos", client: httpx.Client | None = None):
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.client = client or httpx.Client(timeout=30)
        # New-style secret keys (sb_secret_...) go in the apikey header only; legacy service_role JWTs in both.
        self.headers = {"apikey": key}
        if not key.startswith("sb_"):
            self.headers["Authorization"] = f"Bearer {key}"

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code >= 400:
            raise RuntimeError(f"Supabase Storage {resp.status_code}: {resp.text[:200]}")
        return resp

    def ensure_bucket(self) -> str:
        """Creates the private bucket if missing. Returns 'created' or 'exists'."""
        resp = self.client.post(f"{self.base}/bucket", headers=self.headers,
                                json={"id": self.bucket, "name": self.bucket, "public": False})
        if resp.status_code < 300:
            return "created"
        if resp.status_code in (400, 409) and "exist" in resp.text.lower():
            return "exists"
        self._check(resp)
        return "exists"

    def bucket_is_private(self) -> bool:
        resp = self._check(self.client.get(f"{self.base}/bucket/{self.bucket}", headers=self.headers))
        return resp.json().get("public") is False

    def save(self, shop_code: str, data: bytes) -> str:
        path = f"{shop_code}/{uuid.uuid4().hex}.jpg"
        self._check(self.client.post(f"{self.base}/object/{self.bucket}/{path}", content=data,
                                     headers=self.headers | {"Content-Type": "image/jpeg", "x-upsert": "false"}))
        return SB_PREFIX + path

    def get(self, ref: str) -> bytes:
        path = ref.removeprefix(SB_PREFIX)
        return self._check(self.client.get(f"{self.base}/object/{self.bucket}/{path}", headers=self.headers)).content

    def delete(self, ref: str) -> None:
        path = ref.removeprefix(SB_PREFIX)
        self._check(self.client.request("DELETE", f"{self.base}/object/{self.bucket}", headers=self.headers,
                                        json={"prefixes": [path]}))

    def delete_shop(self, shop_code: str) -> None:
        for _ in range(100):  # 1,000 photos per round; the cap stops an endless loop if a delete silently fails
            resp = self._check(self.client.post(f"{self.base}/object/list/{self.bucket}", headers=self.headers,
                                                json={"prefix": shop_code, "limit": 1000, "offset": 0}))
            names = [f"{shop_code}/{o['name']}" for o in resp.json() if o.get("name")]
            if not names:
                return
            self._check(self.client.request("DELETE", f"{self.base}/object/{self.bucket}", headers=self.headers,
                                            json={"prefixes": names}))


def photos_from_env():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if url and key:
        return SupabasePhotos(url, key, os.getenv("PHOTO_BUCKET", "bill-photos").strip() or "bill-photos")
    return LocalPhotos(os.getenv("PHOTO_DIR", "data/raw/bills"))

