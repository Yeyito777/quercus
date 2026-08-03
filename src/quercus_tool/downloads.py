from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from .canvas import Quercus
from .client import MAX_FILE_BYTES
from .errors import NetworkError, UnsafeFileError


def safe_filename(value: str) -> str:
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r"[\x00-\x1f\x7f]", "_", value).strip()
    if value in {"", ".", ".."}:
        value = "quercus-file"
    return value[:240]


def prepare_output_directory(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.exists():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeFileError("file output path is not a real directory")
    else:
        parent = path.parent
        if parent.exists() and parent.is_symlink():
            raise UnsafeFileError("file output parent may not be a symlink")
        try:
            path.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise UnsafeFileError(f"could not create file output directory: {exc.strerror}") from None
    path.chmod(0o700)
    return path.resolve()


def private_write(path: Path, content: bytes, *, force: bool) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise UnsafeFileError("file exceeds the 100 MiB safety limit")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise UnsafeFileError("refused to replace a non-regular output path")
        if not force:
            raise UnsafeFileError(f"file already exists: {path.name}; pass --force to replace it")
    if not force:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            raise UnsafeFileError(f"file already exists: {path.name}; pass --force to replace it") from None
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if fd >= 0:
                os.close(fd)
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


class Downloads:
    def __init__(self, quercus: Quercus):
        self.quercus = quercus

    def download(
        self,
        course_id: int,
        file_id: int,
        *,
        output_directory: str | Path,
        force: bool = False,
    ) -> dict:
        metadata, raw = self.quercus.file(course_id, file_id)
        size = metadata.get("size")
        if isinstance(size, int) and size > MAX_FILE_BYTES:
            raise UnsafeFileError("file exceeds the 100 MiB safety limit")
        candidate = raw.get("url")
        if not isinstance(candidate, str):
            candidate = f"/courses/{course_id}/files/{file_id}/download"
        try:
            candidate = self.quercus.client.validate_canvas_download_url(candidate)
        except NetworkError:
            candidate = self.quercus.client.validate_canvas_download_url(
                f"/courses/{course_id}/files/{file_id}/download"
            )
        response = self.quercus.client.get_bytes(candidate, max_bytes=MAX_FILE_BYTES)
        directory = prepare_output_directory(output_directory)
        path = directory / safe_filename(str(metadata.get("name") or metadata.get("filename") or "quercus-file"))
        private_write(path, response.content, force=force)
        return {
            **metadata,
            "path": str(path),
            "bytes": len(response.content),
            "receivedContentType": response.content_type,
        }
