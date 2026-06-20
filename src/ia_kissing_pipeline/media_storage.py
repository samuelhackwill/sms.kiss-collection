from __future__ import annotations

import mimetypes
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse


MEDIA_KINDS = {
    "preview": ("preview_dir", "previews"),
    "clip": ("clips_dir", "clips"),
}

_S3_CLIENTS = {}


def is_s3_enabled(settings) -> bool:
    return settings.media_storage_backend.strip().lower() == "s3"


def media_root(settings, kind: str) -> Path:
    root_attr, _ = _media_kind(kind)
    return getattr(settings, root_attr)


def media_relpath(settings, kind: str, path_value: str | Path) -> str | None:
    path = Path(path_value)
    root = media_root(settings, kind).resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def has_media_reference(settings, kind: str, path_value: str | Path | None) -> bool:
    if not path_value:
        return False
    path = Path(path_value)
    if path.exists():
        return True
    return is_s3_enabled(settings) and media_relpath(settings, kind, path) is not None


def media_public_url(settings, kind: str, relpath: str) -> str | None:
    if not is_s3_enabled(settings):
        return None
    key = media_key(settings, kind, relpath)
    base_url = settings.media_s3_public_base_url.strip().rstrip("/")
    if not base_url:
        endpoint_url = settings.media_s3_endpoint_url.strip().rstrip("/")
        bucket = settings.media_s3_bucket.strip()
        if not endpoint_url or not bucket:
            return None
        endpoint = urlparse(endpoint_url)
        scheme = endpoint.scheme or "https"
        host = endpoint.netloc or endpoint.path
        base_url = f"{scheme}://{bucket}.{host}"
    return f"{base_url}/{quote(key, safe='/-_.~')}"


def media_key(settings, kind: str, relpath: str) -> str:
    _, prefix = _media_kind(kind)
    clean_relpath = _clean_relpath(relpath)
    return f"{prefix}/{clean_relpath}"


def upload_media_file(
    settings,
    kind: str,
    path_value: str | Path,
    *,
    delete_local_after_upload: bool = False,
) -> str | None:
    if not is_s3_enabled(settings):
        return None
    path = Path(path_value)
    if not path.is_file():
        return None
    relpath = media_relpath(settings, kind, path)
    if relpath is None:
        return None
    key = media_key(settings, kind, relpath)
    extra_args = {}
    content_type, _ = mimetypes.guess_type(path.name)
    if content_type:
        extra_args["ContentType"] = content_type
    if settings.media_s3_cache_control:
        extra_args["CacheControl"] = settings.media_s3_cache_control
    if settings.media_s3_acl:
        extra_args["ACL"] = settings.media_s3_acl
    kwargs = {"ExtraArgs": extra_args} if extra_args else {}
    _s3_client(settings).upload_file(str(path), settings.media_s3_bucket, key, **kwargs)
    if delete_local_after_upload:
        path.unlink(missing_ok=True)
        _remove_empty_media_parents(settings, kind, path.parent)
    return key


def upload_media_tree(
    settings,
    kind: str,
    root_path: str | Path,
    *,
    delete_local_after_upload: bool = False,
) -> list[str]:
    if not is_s3_enabled(settings):
        return []
    root = Path(root_path)
    if not root.exists():
        return []
    uploaded = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            key = upload_media_file(
                settings,
                kind,
                path,
                delete_local_after_upload=delete_local_after_upload,
            )
            if key:
                uploaded.append(key)
    return uploaded


def ensure_local_media_file(settings, kind: str, path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.exists() or not is_s3_enabled(settings):
        return path
    relpath = media_relpath(settings, kind, path)
    if relpath is None:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    _s3_client(settings).download_file(settings.media_s3_bucket, media_key(settings, kind, relpath), str(path))
    return path


def delete_media_file(settings, kind: str, path_value: str | Path) -> None:
    path = Path(path_value)
    relpath = media_relpath(settings, kind, path)
    if path.exists():
        path.unlink()
    if is_s3_enabled(settings) and relpath is not None:
        _s3_client(settings).delete_object(Bucket=settings.media_s3_bucket, Key=media_key(settings, kind, relpath))


def delete_media_tree(settings, kind: str, root_path: str | Path, *, delete_remote: bool = False) -> None:
    root = Path(root_path)
    if root.exists():
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    if not delete_remote or not is_s3_enabled(settings):
        return
    relpath = media_relpath(settings, kind, root)
    if relpath is None:
        return
    delete_media_prefix(settings, kind, relpath)


def delete_media_prefix(settings, kind: str, relpath_prefix: str) -> None:
    if not is_s3_enabled(settings):
        return
    client = _s3_client(settings)
    prefix = media_key(settings, kind, relpath_prefix).rstrip("/") + "/"
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.media_s3_bucket, Prefix=prefix):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if objects:
            client.delete_objects(Bucket=settings.media_s3_bucket, Delete={"Objects": objects})


def prune_cache_directory(cache_dir: str | Path, *, max_bytes: int = 0, max_age_seconds: int = 0) -> dict[str, int]:
    root = Path(cache_dir)
    result = {
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_removed": 0,
        "files_removed": 0,
    }
    if not root.exists():
        return result

    now = time.time()
    files = _cache_files(root)
    result["bytes_before"] = sum(item["size"] for item in files)

    if max_age_seconds > 0:
        cutoff = now - max_age_seconds
        for item in list(files):
            if item["mtime"] < cutoff:
                _remove_cache_file(item["path"], item["size"], result)

    files = _cache_files(root)
    total_bytes = sum(item["size"] for item in files)
    if max_bytes > 0 and total_bytes > max_bytes:
        for item in sorted(files, key=lambda value: value["mtime"]):
            if total_bytes <= max_bytes:
                break
            if _remove_cache_file(item["path"], item["size"], result):
                total_bytes -= item["size"]

    _remove_empty_cache_dirs(root)
    result["bytes_after"] = sum(item["size"] for item in _cache_files(root))
    result["bytes_removed"] = result["bytes_before"] - result["bytes_after"]
    return result


def _media_kind(kind: str) -> tuple[str, str]:
    if kind not in MEDIA_KINDS:
        raise ValueError(f"Unsupported media kind: {kind}")
    return MEDIA_KINDS[kind]


def _remove_empty_media_parents(settings, kind: str, start: Path) -> None:
    root = media_root(settings, kind).resolve()
    current = start.resolve()
    while str(current).startswith(str(root)) and current != root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _cache_files(root: Path) -> list[dict[str, int | float | Path]]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({"path": path, "size": int(stat.st_size), "mtime": float(stat.st_mtime)})
    return files


def _remove_cache_file(path: Path, size: int, result: dict[str, int]) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    result["files_removed"] += 1
    result["bytes_removed"] += size
    return True


def _remove_empty_cache_dirs(root: Path) -> None:
    dirs = (item for item in root.rglob("*") if item.is_dir())
    for path in sorted(dirs, key=lambda value: len(value.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue


def _clean_relpath(relpath: str) -> str:
    path = PurePosixPath(str(relpath).replace("\\", "/"))
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Unsafe media path: {relpath}")
    return path.as_posix().lstrip("/")


def _s3_client(settings):
    if not settings.media_s3_bucket:
        raise RuntimeError("MEDIA_S3_BUCKET is required when MEDIA_STORAGE_BACKEND=s3")
    key = (
        settings.media_s3_endpoint_url,
        settings.media_s3_region,
        settings.media_s3_access_key_id,
        settings.media_s3_bucket,
    )
    if key not in _S3_CLIENTS:
        import boto3

        _S3_CLIENTS[key] = boto3.client(
            "s3",
            endpoint_url=settings.media_s3_endpoint_url or None,
            region_name=settings.media_s3_region or None,
            aws_access_key_id=settings.media_s3_access_key_id or None,
            aws_secret_access_key=settings.media_s3_secret_access_key or None,
        )
    return _S3_CLIENTS[key]
