from __future__ import annotations

import shutil
import ssl
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


def choose_preferred_file(conn, film_id: int, prefer_largest: bool = False):
    size_order = "DESC" if prefer_largest else "ASC"
    row = conn.execute(
        f"""
        SELECT * FROM film_files
        WHERE film_id = ?
        ORDER BY is_preferred_source DESC, is_video DESC, size_bytes {size_order}, id ASC
        LIMIT 1
        """,
        (film_id,),
    ).fetchone()
    return row


def choose_source_files(conn, film_id: int, prefer_largest: bool = False):
    size_order = "DESC" if prefer_largest else "ASC"
    rows = conn.execute(
        f"""
        SELECT * FROM film_files
        WHERE film_id = ?
        ORDER BY is_preferred_source DESC, is_video DESC, size_bytes {size_order}, id ASC
        """,
        (film_id,),
    ).fetchall()
    return rows


def normalize_download_url(source: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme not in ("http", "https"):
        return source
    path = quote(parsed.path, safe="/:@")
    return parsed._replace(path=path).geturl()


def _copy_or_download(source: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = normalize_download_url(source)
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        request = Request(source, headers={"User-Agent": "ia-kissing-pipeline/1.0"})
        try:
            with urlopen(request, timeout=120, context=ssl.create_default_context()) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
        except (ssl.SSLError, URLError) as exc:
            reason = getattr(exc, "reason", exc)
            reason_text = str(reason).lower()
            if "certificate" in reason_text or "ssl" in reason_text:
                try:
                    with urlopen(request, timeout=120, context=ssl._create_unverified_context()) as response, destination.open("wb") as output:
                        shutil.copyfileobj(response, output)
                    return
                except HTTPError as insecure_exc:
                    raise RuntimeError(f"HTTP {insecure_exc.code}: {insecure_exc.reason} (after SSL fallback)") from insecure_exc
                except Exception as insecure_exc:
                    raise RuntimeError(f"SSL fallback failed: {insecure_exc}") from insecure_exc
            raise RuntimeError(f"URL error: {reason}") from exc
        return
    source_path = Path(source)
    shutil.copyfile(source_path, destination)


def ensure_source_video(source_url: str, destination: Path) -> Path:
    if destination.exists():
        return destination
    _copy_or_download(source_url, destination)
    return destination


def build_analysis_path(download_dir: Path, archive_identifier: str) -> Path:
    return download_dir / archive_identifier / "analysis.mp4"


def create_analysis_video(source_path: Path, output_path: Path, max_height: int = 360) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        return output_path
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_path),
            "-vf",
            f"scale=-2:min({max_height}\\,ih)",
            "-r",
            "12",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-an",
            str(output_path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return output_path
