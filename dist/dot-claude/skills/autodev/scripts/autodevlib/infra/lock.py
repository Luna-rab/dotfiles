"""ランディレクトリ 1 つに driver を 1 本だけ走らせる錠（`driver.lock` の flock）。

`events` に書くのはメインループ 1 本だけという前提（ARCHITECTURE §8）は、同じラン名の `autodev run` を
2 つ走らせると崩れる。錠は driver のプロセスが握り、落ちたら OS が外すので、残った錠で止まることは無い。
`clean`・`purge` も、錠が握られている間は worktree とランディレクトリに触らない。
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class DriverBusy(RuntimeError):
    """同じランの driver が、ほかに走っている。"""


class DriverLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> DriverLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(fd)
            raise DriverBusy(f"このランの driver がほかに走っている（{self._path}）") from error
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            # 錠のファイルは消さない。消すと、待っていた側が消える前のファイルの錠を取り、
            # 次に来た側が新しいファイルの錠を取って、2 本が同時に走る
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def held(path: Path) -> bool:
    """ほかのプロセスが錠を握っているか。錠のファイルが無ければ、握られていない。"""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
