import os
import unittest
import sys
from io import StringIO
from pathlib import PurePath
from typing import cast

from report_new_linter_errors import (
    main,
    Sys,
    EXIT_CODE_NO_SNAPSHOT_FILE,
    adjust_line_numbers,
    parse_git_diff_hunks,
    GitDiffHunk,
    DiffLine,
    parse_snapshot_lines,
    SnapshotEntry,
)
import example_linter

stubbed_git = "stubbed-git.bat" if sys.platform == "win32" else "stubbed-git.py"
env_override = {
    "REPORT_NEW_LINTER_ERROR_PATH": os.path.join(os.path.dirname(__file__), "test-tmp"),
    "REPORT_NEW_LINTER_ERROR_GIT_COMMAND": os.path.join(
        os.path.dirname(__file__), "example", stubbed_git
    ),
}
env = os.environ | env_override

snapshot_path = os.path.join(env["REPORT_NEW_LINTER_ERROR_PATH"], "snapshot")


class TestSys:
    argv: list[str]
    stdout: StringIO
    stderr: StringIO

    def __init__(self, argv: list[str]):
        self.argv = argv
        self.stdout = StringIO()
        self.stderr = StringIO()

    @staticmethod
    def exit(status: int) -> None:
        sys.exit(status)


class MainTestCase(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(snapshot_path)
        except FileNotFoundError:
            pass

        test_sys = TestSys(
            ["report_new_linter_errors.py", "python3", "example_linter.py", "setUp"]
        )
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(EXIT_CODE_NO_SNAPSHOT_FILE, cm.exception.code)
        self.assertEqual(
            [
                "Snapshot file not found. Creating a new snapshot file.",
                "Run this command later again to check if new errors are introduced.",
            ],
            test_sys.stderr.getvalue().splitlines()[-2:],
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                example_linter.original_output,
                list(map(str.rstrip, snapshot.readlines())),
                "snapshot file is created",
            )

    def test_new_errors_found(self):
        test_sys = TestSys(
            [
                "report_new_linter_errors.py",
                "python3",
                "example_linter.py",
                "new_errors",
            ]
        )
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            "ERROR: diff command reported that the command may have produced new errors. Fix it or update the snapshot.",
            test_sys.stderr.getvalue().splitlines()[-1],
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                example_linter.original_output,
                list(map(str.rstrip, snapshot.readlines())),
                "snapshot file is NOT updated",
            )

    def test_fewer_errors_found(self):
        test_sys = TestSys(
            [
                "report_new_linter_errors.py",
                "python3",
                "example_linter.py",
                "fewer_errors",
            ]
        )
        main(env, cast(Sys, test_sys))
        self.assertEqual(
            test_sys.stderr.getvalue(),
            "",
            "No error message is printed to stderr",
        )
        self.assertEqual(
            [
                "Congratulations! It looks like that you fixed some errors.",
                "Saving the new snapshot.",
            ],
            list(map(str.rstrip, test_sys.stdout.getvalue().splitlines()))[-2:],
            "stdout contains the message to praise the user",
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                [
                    "---original output 1",
                    "   original output 3",
                ],
                list(map(str.rstrip, snapshot.readlines())),
                "snapshot file is updated with the new output",
            )

    def test_when_no_changes(self):
        test_sys = TestSys(
            ["report_new_linter_errors.py", "python3", "example_linter.py", "setUp"]
        )
        main(env, cast(Sys, test_sys))
        self.assertEqual(
            "",
            test_sys.stderr.getvalue(),
            "No error message is printed to stderr",
        )
        self.assertNotRegex(
            test_sys.stdout.getvalue().rstrip(),
            r"Congratulations! It looks like that you fixed some errors",
            "No message to praise the user is printed to stdout",
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                list(map(str.rstrip, snapshot.readlines())),
                example_linter.original_output,
                "snapshot file is NOT updated",
            )

    def test_when_removed_and_added(self):
        test_sys = TestSys(
            [
                "report_new_linter_errors.py",
                "python3",
                "example_linter.py",
                "removed_and_added",
            ]
        )
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(1, cm.exception.code)
        self.assertEqual(
            "ERROR: diff command reported that the command may have produced new errors. Fix it or update the snapshot.",
            test_sys.stderr.getvalue().splitlines()[-1],
        )
        self.assertEqual(
            "Thank you! It looks like that you fixed some errors. But you also introduced new errors. Fix it!",
            list(map(str.rstrip, test_sys.stdout.getvalue().splitlines()))[-1],
            "stdout contains the new output",
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                example_linter.original_output,
                list(map(str.rstrip, snapshot.readlines())),
                "snapshot file is NOT updated",
            )


class AdjustLineNumbersTestCase(unittest.TestCase):
    def test_increments_line_number1(self):
        with open(
            "./example/diff-prepend-append.diff",
            encoding="utf-8",
        ) as diff_f:
            with open("./example/mypy-base.txt", encoding="utf-8") as base_f:
                actual = adjust_line_numbers(
                    iter(diff_f.readlines()),
                    iter(base_f.readlines()),
                )
                self.assertEqual(
                    [
                        f'example{os.sep}base.py:6: error: Argument 1 to "foo" has incompatible type "str"; expected "int"  [arg-type]',
                        "Found 1 error in 1 file (checked 1 source file)",
                    ],
                    list(actual),
                )

    def test_increments_line_number2(self):
        with open(
            "./example/diff-prepend-more1.diff",
            encoding="utf-8",
        ) as diff_f:
            with open("./example/mypy-base.txt", encoding="utf-8") as base_f:
                actual = adjust_line_numbers(
                    iter(diff_f.readlines()),
                    iter(base_f.readlines()),
                )
                self.assertEqual(
                    [
                        f'example{os.sep}base.py:7: error: Argument 1 to "foo" has incompatible type "str"; expected "int"  [arg-type]',
                        "Found 1 error in 1 file (checked 1 source file)",
                    ],
                    list(actual),
                )

    def test_increments_line_number3(self):
        with open(
            "./example/diff-prepend-more2.diff",
            encoding="utf-8",
        ) as diff_f:
            with open("./example/mypy-base.txt", encoding="utf-8") as base_f:
                actual = adjust_line_numbers(
                    iter(diff_f.readlines()),
                    iter(base_f.readlines()),
                )
                self.assertEqual(
                    [
                        f'example{os.sep}base.py:7: error: Argument 1 to "foo" has incompatible type "str"; expected "int"  [arg-type]',
                        "Found 1 error in 1 file (checked 1 source file)",
                    ],
                    list(actual),
                )

    def test_decreases_line_number(self):
        with open("./example/diff-remove-previous.diff", encoding="utf-8") as diff_f:
            with open("./example/mypy-base.txt", encoding="utf-8") as base_f:
                actual = adjust_line_numbers(
                    iter(diff_f.readlines()),
                    iter(base_f.readlines()),
                )
                self.assertEqual(
                    [
                        f'example{os.sep}base.py:4: error: Argument 1 to "foo" has incompatible type "str"; expected "int"  [arg-type]',
                        "Found 1 error in 1 file (checked 1 source file)",
                    ],
                    list(actual),
                )

    def test_deletes_line(self):
        with open("./example/diff-removed.diff", encoding="utf-8") as diff_f:
            with open("./example/mypy-base.txt", encoding="utf-8") as base_f:
                actual = adjust_line_numbers(
                    iter(diff_f.readlines()),
                    iter(base_f.readlines()),
                )
                self.assertEqual(
                    [
                        "Found 1 error in 1 file (checked 1 source file)",
                    ],
                    list(actual),
                )


class ParseGitDiffHunksTestCase(unittest.TestCase):
    def test_parse_git_diff_hunks(self):
        with open("./example/diff-git.diff", encoding="utf-8") as diff_f:
            actual = list(parse_git_diff_hunks(diff_f))
            expected = [
                GitDiffHunk(
                    path_minus=PurePath(".gitignore"),
                    path_plus=PurePath(".gitignore"),
                    initial_line_number=170,
                    diffs=[
                        " ",
                        " ",
                        " ",
                        DiffLine(line_type="-", content="/report_new_linter_errors"),
                        DiffLine(line_type="+", content="/report-new-linter-errors"),
                    ],
                ),
                GitDiffHunk(
                    path_minus=PurePath("example/delete-previous.py"),
                    path_plus=PurePath("example/delete-previous.py"),
                    initial_line_number=1,
                    diffs=[
                        " ",
                        " ",
                        " ",
                        DiffLine(line_type="-", content=""),
                    ],
                ),
                GitDiffHunk(
                    path_minus=PurePath("report_new_linter_errors.py"),
                    path_plus=PurePath("report_new_linter_errors.py"),
                    initial_line_number=6,
                    diffs=[
                        " ",
                        " ",
                        " ",
                        DiffLine(
                            line_type="-",
                            content="from typing import Protocol, TextIO, Any, cast",
                        ),
                        DiffLine(
                            line_type="+",
                            content="from dataclasses import dataclass",
                        ),
                        DiffLine(
                            line_type="+",
                            content="from typing import Protocol, TextIO, Any, cast, Iterator, Literal, Optional",
                        ),
                        " ",
                        " ",
                        " ",
                    ],
                ),
                GitDiffHunk(
                    path_minus=PurePath("report_new_linter_errors.py"),
                    path_plus=PurePath("report_new_linter_errors.py"),
                    initial_line_number=35,
                    diffs=[
                        " ",
                        " ",
                        " ",
                        DiffLine(
                            line_type="-",
                            content="    snapshot_dir = environ.get(",
                        ),
                        DiffLine(
                            line_type="-",
                            content="    )",
                        ),
                        DiffLine(
                            line_type="-",
                            content="    if not os.path.exists(snapshot_dir):",
                        ),
                        DiffLine(
                            line_type="-",
                            content="        os.makedirs(snapshot_dir)",
                        ),
                        DiffLine(
                            line_type="+",
                            content="    snapshot_dir = SnapshotDirectory.from_environ(environ)",
                        ),
                        " ",
                        DiffLine(
                            line_type="-",
                            content="    snapshot_path = os.path.join(snapshot_dir, 'snapshot')",
                        ),
                        DiffLine(
                            line_type="+",
                            content="    snapshot_path = snapshot_dir.get_snapshot_path()",
                        ),
                        " ",
                        " ",
                        " ",
                        " ",
                        DiffLine(
                            line_type="-",
                            content="        new_snapshot_path = os.path.join(snapshot_dir, 'new-snapshot')",
                        ),
                        DiffLine(
                            line_type="+",
                            content="        new_snapshot_path = snapshot_dir.get_new_snapshot_path()",
                        ),
                        " ",
                        " ",
                        " ",
                    ],
                ),
                GitDiffHunk(
                    path_minus=PurePath("test.py"),
                    path_plus=PurePath("test.py"),
                    initial_line_number=147,
                    diffs=[
                        " ",
                        " ",
                        " ",
                        DiffLine(line_type="+", content=""),
                        DiffLine(
                            line_type="+",
                            content="    class AdjustLineNumbersTestCase(unittest.TestCase):",
                        ),
                        DiffLine(line_type="+", content=""),
                        DiffLine(
                            line_type="+",
                            content="        def test_adjust_line_numbers_increments_line_number(self):",
                        ),
                        DiffLine(
                            line_type="+",
                            content="            pass",
                        ),
                        DiffLine(line_type="+", content=""),
                        DiffLine(line_type="+", content=""),
                    ],
                ),
            ]
            self.assertEqual(expected, actual)


class ParseSnapshotLinesTestCase(unittest.TestCase):
    def test_parse_snapshot_lines(self):
        with open("./example/mypy-base.txt", encoding="utf-8") as snapshot_f:
            actual = list(parse_snapshot_lines(snapshot_f))
            expected = [
                SnapshotEntry(
                    path=PurePath("example") / "base.py",
                    line_number=5,
                    other_contents=': error: Argument 1 to "foo" has incompatible type "str"; expected "int"  [arg-type]',
                ),
                "Found 1 error in 1 file (checked 1 source file)",
            ]
            self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
