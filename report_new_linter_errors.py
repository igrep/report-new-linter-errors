#!/bin/env python

"""
$ report-new-linter-errors <command> [args...]
"""
import os
import shutil
import subprocess
from typing import Protocol, TextIO, Any, cast

# https://stackoverflow.com/questions/1101957/are-there-any-standard-exit-status-codes-in-linux
EXIT_CODE_NO_SNAPSHOT_FILE = 66


class Environ(Protocol):
    def get(self, key: str, default: str) -> str:
        ...


class Sys(Protocol):
    def exit(self, status: int) -> None:
        ...

    @property
    def argv(self) -> list[str]:
        ...

    @property
    def stdout(self) -> TextIO:
        ...

    @property
    def stderr(self) -> TextIO:
        ...


def main(environ: Environ, sys: Sys) -> None:
    snapshot_dir = environ.get(
        'REPORT_NEW_LINTER_ERROR_PATH',
        os.path.join(os.getcwd(), '.report-new-linter-error'),
    )
    if not os.path.exists(snapshot_dir):
        os.makedirs(snapshot_dir)

    snapshot_path = os.path.join(snapshot_dir, 'snapshot')

    linter_command = sys.argv[1:]
    with subprocess.Popen(linter_command, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          text=True, ) as linter_proc:
        new_snapshot_path = os.path.join(snapshot_dir, 'new-snapshot')
        with open(new_snapshot_path, 'w') as f:
            while True:
                chunk = linter_proc.stdout.read(4096)
                if not chunk:
                    break
                f.write(chunk)
                sys.stdout.write(chunk)

    diff_command = environ.get('REPORT_NEW_LINTER_ERROR_DIFF', 'diff')
    if not os.path.exists(snapshot_path):
        print('Snapshot file not found. Creating a new snapshot file.',
              file=cast(Any, sys.stderr))
        shutil.copy(new_snapshot_path, snapshot_path)
        print('Run this command later again to check if new errors are introduced.',
              file=cast(Any, sys.stderr))
        sys.exit(EXIT_CODE_NO_SNAPSHOT_FILE)

    with subprocess.Popen(
            [diff_command, '-u', snapshot_path, new_snapshot_path],
            stdout=subprocess.PIPE,
            text=True,
    ) as diff_proc:
        # Skip header lines
        diff_proc.stdout.readline()
        diff_proc.stdout.readline()

        new_errors_found = False
        some_errors_removed = False
        while True:
            line = diff_proc.stdout.readline()
            if not line:
                break

            if line.startswith('+'):
                sys.stderr.write(line[1:])
                new_errors_found = True
                continue

            if line.startswith('-'):
                some_errors_removed = True
                continue

        if new_errors_found:
            print(
                'ERROR: diff command reported that the command may have produced new errors. Fix it or update the snapshot.',
                file=cast(Any, sys.stderr), )
            if some_errors_removed:
                print(
                    'Thank you! It looks like that you fixed some errors. But you also introduced new errors. Fix it!',
                    file=cast(Any, sys.stdout), )
            sys.exit(1)

        if some_errors_removed:
            print(
                'Congratulations! It looks like that you fixed some errors.',
                file=cast(Any, sys.stdout))
            print('Saving the new snapshot.', file=cast(Any, sys.stdout))
            shutil.copy(new_snapshot_path, snapshot_path)


if __name__ == '__main__':
    import sys as real_sys

    main(os.environ, cast(Sys, real_sys))
