#!/usr/bin/env python3

"""
```
$ report-new-linter-errors snapshot <profile_name> <linter_command> [linter_options...] -- [target_files_or_directories...]
```

Create a profile named `profile_name` by running the `linter_command` with
`linter_options` and `target_files_or_directories` and save the output as a
snapshot.

**NOTE:**
It's recommended to specify `target_files_or_directories` by putting `--` before
them if your linter supports it. The `run` subcommand replaces the
`target_files_or_directories` with the ones that are changed since the last
snapshot, so if you specify all files in the `snapshot` subcommand, you can
just run `report-new-linter-errors run <profile_name>` to check if there are
new errors in the changed files.

```
$ report-new-linter-errors run <profile_name>
```

Check if there are new errors in the changed files since the last snapshot by
running the `linter_command` with the changed files as arguments and comparing
the output with the snapshot.
"""

import os
import re
import shutil
import subprocess
import json
from collections import defaultdict
from dataclasses import dataclass
from itertools import accumulate
from pathlib import PurePath
from typing import Protocol, TextIO, Any, cast, Iterator, Literal, Optional, Union, IO


# https://stackoverflow.com/questions/1101957/are-there-any-standard-exit-status-codes-in-linux
EXIT_CODE_NO_SNAPSHOT_FILE = 66
EXIT_CODE_USAGE = 64


@dataclass(frozen=True)
class CommandConfig:
    cmd_prefix: list[str]
    use_separator: bool

    def to_json_obj(self) -> dict[str, object]:
        return {
            "cmd_prefix": self.cmd_prefix,
            "use_separator": self.use_separator,
        }

    @classmethod
    def from_json_obj(cls, obj: object) -> "CommandConfig":
        # Backward compatibility with older snapshots (stored as a JSON array).
        if isinstance(obj, list):
            if not all(isinstance(x, str) for x in obj):
                raise ValueError("command.json must be a JSON array of strings")
            return cls(cmd_prefix=cast(list[str], obj), use_separator=True)

        if not isinstance(obj, dict):
            raise ValueError("command.json must be a JSON object")

        cmd_prefix = obj.get("cmd_prefix")
        use_separator = obj.get("use_separator")

        if not isinstance(cmd_prefix, list) or not all(
            isinstance(x, str) for x in cmd_prefix
        ):
            raise ValueError("command.json cmd_prefix must be an array of strings")
        if not isinstance(use_separator, bool):
            raise ValueError("command.json use_separator must be a boolean")

        return cls(cmd_prefix=cast(list[str], cmd_prefix), use_separator=use_separator)


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
    argv = sys.argv[1:]
    if not argv:
        print(
            "Usage: report-new-linter-errors snapshot <profile_name> <linter_command> [linter_options...] -- [targets...]\n"
            "       report-new-linter-errors run <profile_name>",
            file=cast(Any, sys.stderr),
        )
        sys.exit(EXIT_CODE_USAGE)

    subcommand = argv[0]
    env_dict = dict(cast(Any, environ))
    if subcommand == "snapshot":
        if len(argv) < 3:
            print(
                "Usage: report-new-linter-errors snapshot <profile_name> <linter_command> [linter_options...] [-- [targets...]]",
                file=cast(Any, sys.stderr),
            )
            sys.exit(EXIT_CODE_USAGE)

        profile_name = argv[1]
        rest = argv[2:]
        use_separator = False
        if "--" in rest:
            sep_index = rest.index("--")
            cmd_prefix = rest[:sep_index]
            targets = rest[sep_index + 1 :]
            use_separator = True
        else:
            cmd_prefix = rest
            targets = []
        if not cmd_prefix:
            print(
                "ERROR: missing linter command",
                file=cast(Any, sys.stderr),
            )
            sys.exit(EXIT_CODE_USAGE)

        snapshot_dir = SnapshotDirectory.from_environ(environ, profile_name)
        new_snapshot_path = snapshot_dir.get_new_snapshot_path()
        snapshot_cmd = list(cmd_prefix)
        if use_separator:
            snapshot_cmd.extend(["--", *targets])
        elif targets:
            snapshot_cmd.extend(targets)
        _run_command_to_snapshot(
            cmd=snapshot_cmd,
            snapshot_path=new_snapshot_path,
            sys_stdout=sys.stdout,
            env=env_dict,
        )
        shutil.copy(new_snapshot_path, snapshot_dir.get_snapshot_path())
        save_current_commit(environ, snapshot_dir, env_dict=env_dict)
        snapshot_dir.save_command_config(
            CommandConfig(cmd_prefix=cmd_prefix, use_separator=use_separator)
        )
        return

    if subcommand == "run":
        if len(argv) != 2:
            print(
                "Usage: report-new-linter-errors run <profile_name>",
                file=cast(Any, sys.stderr),
            )
            sys.exit(EXIT_CODE_USAGE)

        profile_name = argv[1]
        snapshot_dir = SnapshotDirectory.from_environ(environ, profile_name)
        snapshot_path = snapshot_dir.get_snapshot_path()
        commit_path = snapshot_dir.get_commit_path()
        command_path = snapshot_dir.get_command_path()

        if not os.path.exists(snapshot_path) or not os.path.exists(commit_path):
            print(
                f"Snapshot for profile '{profile_name}' not found. Run: report-new-linter-errors snapshot {profile_name} ...",
                file=cast(Any, sys.stderr),
            )
            sys.exit(EXIT_CODE_NO_SNAPSHOT_FILE)

        if not os.path.exists(command_path):
            print(
                f"ERROR: Missing command.json for profile '{profile_name}'. Recreate the snapshot.",
                file=cast(Any, sys.stderr),
            )
            sys.exit(EXIT_CODE_USAGE)

        snapshot_commit = snapshot_dir.get_current_commit()
        if snapshot_commit is None:
            print(
                f"Snapshot for profile '{profile_name}' is missing commit anchor. Recreate the snapshot.",
                file=cast(Any, sys.stderr),
            )
            sys.exit(EXIT_CODE_NO_SNAPSHOT_FILE)

        git_command = environ.get("REPORT_NEW_LINTER_ERROR_GIT_COMMAND", "git")
        changed_files = subprocess.check_output(
            [
                git_command,
                "diff",
                "--name-only",
                f"{snapshot_commit}..HEAD",
            ],
            text=True,
            env=env_dict,
        ).splitlines()
        changed_files = [p for p in changed_files if p.strip()]
        if not changed_files:
            return

        command_config = snapshot_dir.load_command_config()
        new_snapshot_path = snapshot_dir.get_new_snapshot_path()
        run_cmd = list(command_config.cmd_prefix)
        if command_config.use_separator:
            run_cmd.append("--")
        run_cmd.extend(changed_files)
        _run_command_to_snapshot(
            cmd=run_cmd,
            snapshot_path=new_snapshot_path,
            sys_stdout=sys.stdout,
            env=env_dict,
        )

        # Adjust snapshot line numbers in memory (do NOT rewrite snapshot on disk).
        linted_files_diff = subprocess.check_output(
            [
                git_command,
                "diff",
                "--unified",
                f"{snapshot_commit}..HEAD",
            ],
            text=True,
            env=env_dict,
        ).splitlines(keepends=True)
        with open(snapshot_path, "r") as f:
            snapshot_lines = f.readlines()
        adjusted_snapshot_lines = adjust_line_numbers(
            iter_diff_lines=iter(linted_files_diff),
            iter_snapshot_lines=iter(snapshot_lines),
        )

        changed_file_paths = {PurePath(p) for p in changed_files}
        snapshot_entries_for_changed_files = {
            sl
            for sl in parse_snapshot_lines(iter(adjusted_snapshot_lines))
            if isinstance(sl, SnapshotEntry) and sl.path in changed_file_paths
        }
        with open(new_snapshot_path, "r") as f:
            new_snapshot_lines = [line.rstrip("\n") for line in f.readlines()]
        new_entries = {
            sl
            for sl in parse_snapshot_lines(iter(new_snapshot_lines))
            if isinstance(sl, SnapshotEntry)
        }

        new_errors = new_entries - snapshot_entries_for_changed_files
        removed_errors = snapshot_entries_for_changed_files - new_entries
        for entry in sorted(
            new_errors,
            key=lambda e: (str(e.path), e.line_number, e.other_contents),
        ):
            sys.stderr.write(f"{format_snapshot_line(entry)}\n")

        if removed_errors:
            print(
                "Congratulations! It looks like that you fixed some errors.",
                file=cast(Any, sys.stdout),
            )

        if new_errors:
            print(
                "ERROR: linter reported new errors in changed files. Fix it or update the snapshot.",
                file=cast(Any, sys.stderr),
            )
            sys.exit(1)
        return

    print(
        f"Unknown subcommand: {subcommand}",
        file=cast(Any, sys.stderr),
    )
    sys.exit(EXIT_CODE_USAGE)


def _run_command_to_snapshot(
    *,
    cmd: list[str],
    snapshot_path: str,
    sys_stdout: TextIO,
    env: dict[str, str],
) -> None:
    # Combine stderr into stdout so we don't deadlock on PIPEs.
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    ) as proc:
        stdout = cast(IO[str], proc.stdout)
        with open(snapshot_path, "w") as f:
            while True:
                chunk = stdout.read(4096)
                if not chunk:
                    break
                f.write(chunk)
                sys_stdout.write(chunk)
        proc.wait()


class SnapshotDirectory:
    def __init__(self, root: str, profile_name: str):
        self.root = root
        self.profile_name = profile_name
        self.profile_root = os.path.join(root, profile_name)

    @classmethod
    def from_environ(cls, environ: Environ, profile_name: str) -> "SnapshotDirectory":
        root = environ.get(
            "REPORT_NEW_LINTER_ERROR_PATH",
            os.path.join(os.getcwd(), ".report-new-linter-errors"),
        )
        if not os.path.exists(root):
            os.makedirs(root)
        profile_root = os.path.join(root, profile_name)
        if not os.path.exists(profile_root):
            os.makedirs(profile_root)
        return cls(root, profile_name)

    def get_profile_root(self) -> str:
        return self.profile_root

    def get_snapshot_path(self) -> str:
        return os.path.join(self.profile_root, "snapshot")

    def get_new_snapshot_path(self) -> str:
        return os.path.join(self.profile_root, "new-snapshot")

    def get_commit_path(self) -> str:
        return os.path.join(self.profile_root, "commit")

    def get_command_path(self) -> str:
        return os.path.join(self.profile_root, "command.json")

    def get_current_commit(self) -> Optional[str]:
        commit_path = self.get_commit_path()
        if not os.path.exists(commit_path):
            return None
        with open(commit_path, "r") as f:
            return f.read().strip()

    def save_current_commit(self, commit: str) -> None:
        commit_path = self.get_commit_path()
        with open(commit_path, "w") as f:
            f.write(commit)

    def save_command_config(self, config: CommandConfig) -> None:
        command_path = self.get_command_path()
        with open(command_path, "w") as f:
            json.dump(config.to_json_obj(), f)

    def load_command_config(self) -> CommandConfig:
        command_path = self.get_command_path()
        with open(command_path, "r") as f:
            contents = json.load(f)
        return CommandConfig.from_json_obj(contents)


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
        env=dict(cast(Any, environ)),
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
    *,
    env_dict: Optional[dict[str, str]] = None,
) -> None:
    git_command = environ.get("REPORT_NEW_LINTER_ERROR_GIT_COMMAND", "git")
    new_commit = subprocess.check_output(
        [git_command, "rev-parse", "HEAD"],
        text=True,
        env=env_dict or dict(cast(Any, environ)),
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
        if line_number <= 0:
            return
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


re_separator = re.compile(r"[, ]")


def parse_diff_current_initial_line_number(line: str) -> int:
    # Example: @@ -1,4 +1,5 @@
    return int(re_separator.split(line[len("@@ -") :])[0])


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
        line = line.rstrip("\n")
        if line.startswith("--- "):
            if current_path_minus is None:
                current_path_minus = parse_git_diff_file_path("---", line)
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
                current_path_minus = parse_git_diff_file_path("---", line)
        elif line.startswith("+++ "):
            current_path_plus = parse_git_diff_file_path("+++", line)
        elif line.startswith("@@ "):
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
            current_initial_line_number = parse_diff_current_initial_line_number(line)
        elif line.startswith(" "):
            current_diffs.append(" ")
        elif line.startswith("+"):
            current_diffs.append(DiffLine(line_type="+", content=line[1:]))
        elif line.startswith("-"):
            current_diffs.append(DiffLine(line_type="-", content=line[1:]))
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
