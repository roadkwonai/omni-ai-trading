"""Exclusive file locks on every OS: one program at a time, released by the OS when the holder closes the file or dies.

POSIX uses flock. Windows has no flock, so one byte far past the end is locked with msvcrt (Windows refuses other programs
to read a locked byte, and the lock must not touch what the file holds). Both belong to the open file, so a second open of
the same file in the same process is refused too.
"""
from __future__ import annotations

import errno
import os
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # everywhere else
    msvcrt = None

FAR = 0x7FFF0000
WAIT = 0.05


def _fd(file):
    return file if isinstance(file, int) else file.fileno()


def _locking(fd, mode):
    """One msvcrt.locking call at FAR; the file position stays where it was."""
    at = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, FAR, os.SEEK_SET)
    try:
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, at, os.SEEK_SET)


def lock(file, blocking=True, sleep=time.sleep):
    """Lock an open file (or descriptor). Not blocking: BlockingIOError at once when another holder has it."""
    fd = _fd(file)
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    if msvcrt is None:
        raise OSError(errno.ENOLCK, 'no file lock on this system')
    while True:
        try:
            _locking(fd, msvcrt.LK_NBLCK)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EDEADLK):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, 'locked by another program') from exc
        sleep(WAIT)


def unlock(file):
    fd = _fd(file)
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        _locking(fd, msvcrt.LK_UNLCK)
