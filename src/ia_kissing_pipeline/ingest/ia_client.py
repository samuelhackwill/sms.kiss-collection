from __future__ import annotations

import json
import ssl
import time
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.error import URLError
from urllib.request import Request, urlopen


SEARCH_FIELDS = [
    "identifier",
    "title",
    "year",
    "description",
    "subject",
    "creator",
    "collection",
    "language",
    "runtime",
    "licenseurl",
]


class IAClient:
    def __init__(self, cache_dir: Path, user_agent: str, throttle_seconds: float = 0.5) -> None:
        self.cache_dir = cache_dir / "ia"
        self.search_cache_dir = self.cache_dir / "search"
        self.metadata_cache_dir = self.cache_dir / "metadata"
        self.user_agent = user_agent
        self.throttle_seconds = throttle_seconds
        self.search_cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_cache_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_json(self, url: str, cache_path: Path) -> dict:
        if cache_path.exists():
            return json.loads(cache_path.read_text())

        request = Request(url, headers={"User-Agent": self.user_agent})
        payload = None
        try:
            with urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (ssl.SSLError, URLError) as exc:
            reason = getattr(exc, "reason", exc)
            reason_text = str(reason).lower()
            if "certificate" not in reason_text and "ssl" not in reason_text:
                raise
            # Some VPS environments ship a broken CA chain. Fall back so the
            # pipeline remains operable for public Internet Archive metadata.
            insecure_context = ssl._create_unverified_context()
            with urlopen(request, timeout=30, context=insecure_context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        time.sleep(self.throttle_seconds)
        return payload

    def fetch_search_page(self, query: str, page: int, rows: int) -> dict:
        params = [("q", query), ("rows", rows), ("page", page), ("output", "json")]
        for field in SEARCH_FIELDS:
            params.append(("fl[]", field))
        url = f"https://archive.org/advancedsearch.php?{urlencode(params)}"
        cache_key = quote(query, safe="").replace("%", "_")
        cache_path = self.search_cache_dir / f"{cache_key}_page_{page}_rows_{rows}.json"
        return self._fetch_json(url, cache_path)

    def fetch_metadata(self, identifier: str) -> dict:
        url = f"https://archive.org/metadata/{quote(identifier)}"
        cache_path = self.metadata_cache_dir / f"{identifier}.json"
        return self._fetch_json(url, cache_path)
