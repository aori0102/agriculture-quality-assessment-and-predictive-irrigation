"""Platform-specific file operations; Windows permissions use inherited ACLs."""

import errno
import os
from pathlib import Path
import stat

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def has_public_permissions(mode: int) -> bool:
    """POSIX mode bits do not describe Windows ACL permissions."""
    return os.name != "nt" and bool(mode & 0o077)


def make_private(fd: int) -> None:
    if os.name != "nt":
        os.fchmod(fd, 0o600)


def sync_directory(path: Path) -> None:
    """Persist a rename on POSIX; Windows cannot open directories via os.open."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def acquire_instance_lock(path: Path) -> int:
    """Return an owned descriptor; closing it releases the process lock."""
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if has_public_permissions(path.parent.stat().st_mode):
        raise PermissionError("Edge runtime directory must be private (0700).")
    if path.is_symlink():
        raise PermissionError("Edge lock file must not be a symlink.")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or has_public_permissions(mode):
            raise PermissionError("Edge lock file must be private (0600).")
        if os.name == "nt":
            # Windows permits locking beyond EOF; no writes or truncation needed.
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise BlockingIOError(errno.EAGAIN, "Edge instance lock is held.") from error
                raise
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd
