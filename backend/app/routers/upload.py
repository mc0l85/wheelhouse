"""/api/upload — pasted images. The browser can't hand a clipboard image to a
program in the PTY, so the page posts the bytes here, gets a file path back and
pastes that path into the pane (Claude Code turns a pasted image path into an
image attachment; other agents can read the file)."""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .. import herdr
from ..config import settings

router = APIRouter(prefix="/api/upload", tags=["upload"])

# Sniffed from the bytes, never taken from Content-Type.
_MAGIC = [(b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"),
          (b"GIF87a", "gif"), (b"GIF89a", "gif")]
_KEEP_S = 7 * 86400          # uploads older than this are pruned on the next one
_KEEP_N = 200


def _kind(data: bytes) -> str | None:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def upload_dir() -> Path:
    d = Path(settings.state_dir).expanduser() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prune(d: Path) -> None:
    files = sorted(d.glob("paste-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - _KEEP_S
    for i, p in enumerate(files):
        if i >= _KEEP_N or p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)


@router.post("/image")
async def image(request: Request) -> dict:
    if not settings.enable_writes:           # the path lands in a live pane
        raise herdr.WritesDisabled("uploading an image needs HERDRWEB_ENABLE_WRITES=1")
    limit = settings.upload_max_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(413, f"image over {settings.upload_max_mb} MB")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise HTTPException(413, f"image over {settings.upload_max_mb} MB")
    ext = _kind(bytes(data[:16]))
    if not ext:
        raise HTTPException(415, "not a PNG, JPEG, GIF or WebP image")
    d = upload_dir()
    _prune(d)
    path = d / f"paste-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.{ext}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return {"path": str(path), "bytes": len(data), "type": ext}
