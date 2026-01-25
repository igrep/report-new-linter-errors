#!/bin/env python3

"""
$ report-new-linter-errors <command> [args...]
"""

import os
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from itertools import accumulate
from pathlib import PurePath
from typing import Protocol, TextIO, Any, cast, Iterator, Literal, Optional, Union


# https://stackoverflow.com/questions/1101957/are-there-any-standard-exit-status-codes-in-linux
EXIT_CODE_NO_SNAPSHOT_FILE = 66


class Environ(Protocol):
    def get(self, key: str, default: str) -> str: ...


class Sys(Protocol):
    def exit(self, status: int) -> None: ...

    @property
    def argv(self) -> list[str]: ...

    @property
    def stdout(self) -> TextIO: ...

    @property
    def stderr(self) -> TextIO: ...


def main(environ: Environ, sys: Sys) -> None:
    snapshot_dir = SnapshotDirectory.from_environ(environ)

    snapshot_path = snapshot_dir.get_snapshot_path()

    linter_command = sys.argv[1:]
    with subprocess.Popen(
        linter_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as linter_proc:
        new_snapshot_path = snapshot_dir.get_new_snapshot_path()
        with open(new_snapshot_path, "w") as f:
            while True:
                chunk = linter_proc.stdout.read(4096)
                if not chunk:
                    break
                f.write(chunk)
                sys.stdout.write(chunk)

    diff_command = environ.get("REPORT_NEW_LINTER_ERROR_DIFF", "diff")
    if not os.path.exists(snapshot_path):
        print(
            "Snapshot file not found. Creating a new snapshot file.",
            file=cast(Any, sys.stderr),
        )
        shutil.copy(new_snapshot_path, snapshot_path)
        print(
            "Run this command later again to check if new errors are introduced.",
            file=cast(Any, sys.stderr),
        )
        sys.exit(EXIT_CODE_NO_SNAPSHOT_FILE)

    adjust_line_numbers_in_snapshot(environ, snapshot_dir)
    save_current_commit(environ, snapshot_dir)

    with subprocess.Popen(
        [diff_command, "-u", snapshot_path, new_snapshot_path],
        stdout=subprocess.PIPE,
        text=True,
    ) as diff_proc:
        stdout = diff_proc.stdout
        # Skip header lines
        stdout.readline()
        stdout.readline()

        new_errors_found = False
        some_errors_removed = False
        while True:
            l = stdout.readline().rstrip("\n")
            if not l:
                break

            if l.startswith("+"):
                sys.stderr.write(f"{l[1:]}\n")
                new_errors_found = True
                continue

            if l.startswith("-"):
                some_errors_removed = True
                continue

        if new_errors_found:
            print(
                "ERROR: diff command reported that the command may have produced new errors. Fix it or update the snapshot.",
                file=cast(Any, sys.stderr),
            )
            if some_errors_removed:
                print(
                    "Thank you! It looks like that you fixed some errors. But you also introduced new errors. Fix it!",
                    file=cast(Any, sys.stdout),
                )
            sys.exit(1)

        if some_errors_removed:
            print(
                "Congratulations! It looks like that you fixed some errors.",
                file=cast(Any, sys.stdout),
            )
            print("Saving the new snapshot.", file=cast(Any, sys.stdout))
            shutil.copy(new_snapshot_path, snapshot_path)


class SnapshotDirectory:
    def __init__(self, root: str):
        self.root = root

    @classmethod
    def from_environ(cls, environ: Environ) -> "SnapshotDirectory":
        root = environ.get(
            "REPORT_NEW_LINTER_ERROR_PATH",
            os.path.join(os.getcwd(), ".report-new-linter-errors"),
        )
        if not os.path.exists(root):
            os.makedirs(root)
        return cls(root)

    def get_snapshot_path(self) -> str:
        return os.path.join(self.root, "snapshot")

    def get_new_snapshot_path(self) -> str:
        return os.path.join(self.root, "new-snapshot")

    def get_current_commit(self) -> Optional[str]:
        commit_path = os.path.join(self.root, "commit")
        if not os.path.exists(commit_path):
            return None
        with open(commit_path, "r") as f:
            return f.read().strip()

    def save_current_commit(self, commit: str) -> None:
        commit_path = os.path.join(self.root, "commit")
        with open(commit_path, "w") as f:
            f.write(commit)


def adjust_line_numbers_in_snapshot(
    environ: Environ,
    snapshot_dir: SnapshotDirectory,
) -> None:
    current_commit = snapshot_dir.get_current_commit()
    if current_commit is None:
        return
    git_command = environ.get("REPORT_NEW_LINTER_ERROR_GIT_COMMAND", "git")
    linted_files_diff = subprocess.check_output(
        [git_command, "diff", "--unified", f"{current_commit}..HEAD"],
        text=True,
    ).splitlines(keepends=True)
    with open(snapshot_dir.get_snapshot_path(), "r") as f:
        snapshot_lines = f.readlines()
        adjusted_snapshot_lines = adjust_line_numbers(
            iter_diff_lines=iter(linted_files_diff),
            iter_snapshot_lines=iter(snapshot_lines),
        )
    with open(snapshot_dir.get_snapshot_path(), "w") as f:
        f.writelines("\n".join(adjusted_snapshot_lines) + "\n")


def save_current_commit(
    environ: Environ,
    snapshot_dir: SnapshotDirectory,
) -> None:
    git_command = environ.get("REPORT_NEW_LINTER_ERROR_GIT_COMMAND", "git")
    new_commit = subprocess.check_output(
        [git_command, "rev-parse", "HEAD"],
        text=True,
    ).strip()
    snapshot_dir.save_current_commit(new_commit)


@dataclass(frozen=True)
class DiffLine:
    """
    Represents each line in a hunk in the output of `git diff -u`
    """

    line_type: Literal["+", "-"]
    content: str


ContextLine = Literal[" "]

DiffPayloadLine = Union[ContextLine, DiffLine]


@dataclass(frozen=True)
class GitDiffHunk:
    """
    Represents each hunk in the output of `git diff -u`
    """

    path_minus: PurePath
    path_plus: PurePath
    initial_line_number: int
    diffs: list[DiffPayloadLine]


@dataclass(frozen=True)
class SnapshotEntry:
    """
    Represents each entry in the output of `mypy`
    (Supports other linters in the future)
    """

    path: PurePath
    line_number: int
    other_contents: str


SnapshotLine = Union[str, SnapshotEntry]


def format_snapshot_line(snapshot_line: SnapshotLine) -> str:
    if isinstance(snapshot_line, str):
        return snapshot_line
    return f"{snapshot_line.path}:{snapshot_line.line_number}{snapshot_line.other_contents}"


class AdjustmentTable:
    _contents: defaultdict[
        PurePath,
        list[int],
        # ^ index by original line number, value is offset to add.
    ]

    def __init__(self):
        self._contents = defaultdict(list)

    @staticmethod
    class AccumulatedOffsets:
        _offsets: list[int]  # key is original line number - 1, value is total offset

        def __init__(self, non_accumulated_offsets: list[int]):
            self._offsets = list(accumulate(non_accumulated_offsets))

        def __getitem__(self, line_number: int) -> int:
            line_index = line_number - 1
            if line_index < 0:
                return 0
            if line_index >= len(self._offsets):
                return self._offsets[-1]
            return self._offsets[line_index]

    def add_offset_of(
        self,
        path: PurePath,
        line_number: int,
        offset: int,
    ) -> None:
        line_index = line_number - 1  # zero-based index
        if len(self._contents[path]) <= line_index:
            self._contents[path].extend(
                [0] * (line_index + 1 - len(self._contents[path]))
            )
        self._contents[path][line_index] += offset

    def lines_and_total_offsets(self) -> dict[PurePath, AccumulatedOffsets]:
        return {
            p: AdjustmentTable.AccumulatedOffsets(offsets)
            for (p, offsets) in self._contents.items()
        }


class CollectedSnapshotEntries:
    _snapshot_lines: list[Optional[SnapshotLine]]  # None if deleted
    _index: defaultdict[
        PurePath,  # path
        dict[
            int,  # line_number
            int,  # index in _snapshot_lines
        ],
    ]

    def __init__(self, iter_snapshot_lines: Iterator[str]) -> None:
        self._snapshot_lines = []
        self._index = defaultdict(lambda: {})
        for i, line in enumerate(parse_snapshot_lines(iter_snapshot_lines)):
            self._snapshot_lines.append(line)
            if isinstance(line, SnapshotEntry):
                self._index[line.path][line.line_number] = i

    def to_formatted_snapshot_lines(self) -> list[str]:
        return [
            format_snapshot_line(sl) for sl in self._snapshot_lines if sl is not None
        ]

    def get_entry(
        self,
        path_minus: PurePath,
        line_number: int,
    ) -> Optional[SnapshotEntry]:
        line_index = self._index.get(
            path_minus,
            {},
        ).get(line_number)
        if line_index is None:
            return None
        line = self._snapshot_lines[line_index]
        if isinstance(line, SnapshotEntry):
            return line
        return None

    def delete_entry(
        self,
        path_minus: PurePath,
        line_number: int,
    ):
        index_in_path = self._index.get(path_minus)
        if index_in_path is None:
            return
        line_index = index_in_path.get(line_number)
        if line_index is None:
            return
        self._snapshot_lines[line_index] = None
        del index_in_path[line_number]

    def adjust_entry_line_numbers(
        self,
        table: AdjustmentTable,
    ):
        """
        Adjust line numbers of snapshot entries according to adjustment table.
        NOTE: This method does not update the index.

        :param table:
        :return:
        """
        for path, offsets in table.lines_and_total_offsets().items():
            index_in_path = self._index.get(path)
            if index_in_path is None:
                continue
            for original_line_number, line_index in index_in_path.items():
                entry = self._snapshot_lines[line_index]
                if not isinstance(entry, SnapshotEntry):
                    continue
                offset = offsets[original_line_number]
                new_line_number = entry.line_number + offset
                new_entry = SnapshotEntry(
                    path=entry.path,
                    line_number=new_line_number,
                    other_contents=entry.other_contents,
                )
                self._snapshot_lines[line_index] = new_entry


def adjust_line_numbers(
    iter_diff_lines: Iterator[str],
    iter_snapshot_lines: Iterator[str],
) -> list[str]:
    """
    :param iter_diff_lines:
    :param iter_snapshot_lines:
    :return: list of snapshot lines with adjusted line numbers

    Adjust line numbers in iter_snapshot_lines according to linted_files_diff
    """
    adjustment_table = AdjustmentTable()
    snapshot_entries = CollectedSnapshotEntries(iter_snapshot_lines)
    iter_diff_hunk = parse_git_diff_hunks(iter_diff_lines)
    for diff_hunk in iter_diff_hunk:
        original_line_number_offset = diff_hunk.initial_line_number
        for i, diff_line in enumerate(diff_hunk.diffs):
            original_line_number = original_line_number_offset + i
            snapshot_line = snapshot_entries.get_entry(
                diff_hunk.path_minus,
                original_line_number,
            )

            if snapshot_line is None:
                if diff_line == " ":
                    adjustment_table.add_offset_of(
                        diff_hunk.path_minus,
                        original_line_number,
                        0,
                    )
                elif diff_line.line_type == "-":
                    # Previous line is deleted
                    adjustment_table.add_offset_of(
                        diff_hunk.path_minus,
                        original_line_number,
                        -1,
                    )
                elif diff_line.line_type == "+":
                    # Previous line is added
                    original_line_number_offset -= 1
                    adjustment_table.add_offset_of(
                        diff_hunk.path_minus,
                        original_line_number,
                        +1,
                    )

            elif diff_line == " ":
                # snapshot line isn't changed
                adjustment_table.add_offset_of(
                    diff_hunk.path_minus,
                    original_line_number,
                    0,
                )
            elif diff_line.line_type == "-":
                # line in the snapshot is deleted
                snapshot_entries.delete_entry(
                    diff_hunk.path_minus,
                    original_line_number,
                )
                adjustment_table.add_offset_of(
                    diff_hunk.path_minus,
                    original_line_number,
                    -1,
                )
            elif diff_line.line_type == "+":
                # line different from the one in the snapshot is added
                original_line_number_offset -= 1

                adjustment_table.add_offset_of(
                    diff_hunk.path_minus,
                    original_line_number,
                    1,
                )

    snapshot_entries.adjust_entry_line_numbers(adjustment_table)
    return snapshot_entries.to_formatted_snapshot_lines()


def parse_git_diff_file_path(
    sign: Literal["---", "+++"],
    line: str,
) -> PurePath:
    return PurePath(drop_git_diff_prefix(line[len(sign) + 1 :]))


def drop_git_diff_prefix(path: str) -> str:
    regex = re.compile(r"^[ab]/")
    return regex.sub("", path)


def parse_diff_current_initial_line_number(line: str) -> int:
    # Example: @@ -1,4 +1,5 @@
    return int(line[len("@@ -") :].split(",", 1)[0])


def parse_git_diff_hunks(
    iter_diff_lines: Iterator[str],
) -> Iterator[GitDiffHunk]:
    current_path_minus: Optional[PurePath] = None
    current_path_plus: Optional[PurePath] = None
    current_initial_line_number: Optional[int] = None
    current_diffs: list[DiffPayloadLine] = []

    is_empty = True
    for line in iter_diff_lines:
        is_empty = False
        l = line.rstrip("\n")
        if l.startswith("--- "):
            if current_path_minus is None:
                current_path_minus = parse_git_diff_file_path("---", l)
            else:
                if current_path_plus is None:
                    raise ValueError("Malformed diff hunk: missing +++ line")
                if current_initial_line_number is None:
                    raise ValueError("Malformed diff hunk: missing @@ line")

                yield GitDiffHunk(
                    path_minus=current_path_minus,
                    path_plus=current_path_plus,
                    initial_line_number=current_initial_line_number,
                    diffs=current_diffs,
                )
                current_diffs = []
                current_path_minus = parse_git_diff_file_path("---", l)
        elif l.startswith("+++ "):
            current_path_plus = parse_git_diff_file_path("+++", l)
        elif l.startswith("@@ "):
            if current_diffs:
                if current_path_minus is None:
                    raise ValueError("Malformed diff hunk: missing --- line")
                if current_path_plus is None:
                    raise ValueError("Malformed diff hunk: missing +++ line")
                if current_initial_line_number is None:
                    raise ValueError("Malformed diff hunk: missing @@ line")

                yield GitDiffHunk(
                    path_minus=current_path_minus,
                    path_plus=current_path_plus,
                    initial_line_number=current_initial_line_number,
                    diffs=current_diffs,
                )
                current_diffs = []
            current_initial_line_number = parse_diff_current_initial_line_number(l)
        elif l.startswith(" "):
            current_diffs.append(" ")
        elif l.startswith("+"):
            current_diffs.append(DiffLine(line_type="+", content=l[1:]))
        elif l.startswith("-"):
            current_diffs.append(DiffLine(line_type="-", content=l[1:]))
        else:
            pass  # ignore other lines

    if is_empty:
        return

    if current_path_minus is None:
        raise ValueError("Malformed diff hunk: missing --- line")
    if current_path_plus is None:
        raise ValueError("Malformed diff hunk: missing +++ line")
    if current_initial_line_number is None:
        raise ValueError("Malformed diff hunk: missing @@ line")

    yield GitDiffHunk(
        path_minus=current_path_minus,
        path_plus=current_path_plus,
        initial_line_number=current_initial_line_number,
        diffs=current_diffs,
    )


re_snapshot_line = re.compile(r"^(.+):(\d+)(.*)")


def parse_snapshot_lines(
    iter_diff_lines: Iterator[str],
) -> Iterator[SnapshotLine]:
    for line in iter_diff_lines:
        m = re_snapshot_line.match(line.rstrip("\n"))
        if m is None:
            yield line.rstrip("\n")
            continue
        (path, line_number_str, other_contents) = m.groups()
        yield SnapshotEntry(
            path=PurePath(path),
            line_number=int(line_number_str),
            other_contents=other_contents,
        )


if __name__ == "__main__":
    import sys as real_sys

    main(os.environ, cast(Sys, real_sys))
