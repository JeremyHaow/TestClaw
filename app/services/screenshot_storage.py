import asyncio
import logging
import mimetypes
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _clean_prefix(value: str) -> str:
    return "/".join(part.strip("/") for part in value.split("/") if part.strip("/"))


def _object_key(run_id: str, filename: str) -> str:
    prefix = _clean_prefix(settings.OSS_PREFIX or "testclaw/screenshots")
    return f"{prefix}/{run_id}/{filename}" if prefix else f"{run_id}/{filename}"


def _public_url(key: str) -> str | None:
    if settings.OSS_PUBLIC_BASE_URL:
        return f"{settings.OSS_PUBLIC_BASE_URL.rstrip('/')}/{key}"
    if settings.OSS_ENDPOINT:
        endpoint = settings.OSS_ENDPOINT
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            endpoint_host = endpoint.split("://", 1)[1].rstrip("/")
            scheme = endpoint.split("://", 1)[0]
        else:
            endpoint_host = endpoint.rstrip("/")
            scheme = "https"
        return f"{scheme}://{settings.OSS_BUCKET}.{endpoint_host}/{key}"
    if settings.OSS_REGION:
        return f"https://{settings.OSS_BUCKET}.oss-{settings.OSS_REGION}.aliyuncs.com/{key}"
    return None


def oss_configured() -> bool:
    return bool(settings.OSS_ENABLED and settings.OSS_BUCKET and settings.OSS_REGION)


@lru_cache(maxsize=1)
def _client():
    import alibabacloud_oss_v2 as oss

    credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
    cfg = oss.config.load_default()
    cfg.credentials_provider = credentials_provider
    cfg.region = settings.OSS_REGION
    if settings.OSS_ENDPOINT:
        cfg.endpoint = settings.OSS_ENDPOINT
    cfg.use_cname = settings.OSS_USE_CNAME
    return oss.Client(cfg)


def _upload_sync(path: Path, run_id: str) -> dict:
    if not oss_configured():
        return {"backend": "local", "path": str(path)}

    import alibabacloud_oss_v2 as oss

    key = _object_key(run_id, path.name)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        body = handle.read()

    try:
        result = _client().put_object(
            oss.PutObjectRequest(
                bucket=settings.OSS_BUCKET,
                key=key,
                body=body,
                content_type=content_type,
            )
        )
        return {
            "backend": "oss",
            "bucket": settings.OSS_BUCKET,
            "key": key,
            "url": _public_url(key),
            "etag": getattr(result, "etag", None),
            "request_id": getattr(result, "request_id", None),
            "status_code": getattr(result, "status_code", None),
        }
    except Exception as exc:
        logger.warning("Failed to upload screenshot to OSS: %s", exc)
        return {
            "backend": "local",
            "path": str(path),
            "oss_error": str(exc),
        }


async def store_screenshot(path: Path, run_id: str) -> dict:
    if not path.exists():
        return {"backend": "missing", "path": str(path)}
    return await asyncio.to_thread(_upload_sync, path, run_id)
