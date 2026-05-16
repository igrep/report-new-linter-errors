import os
import sys
import unittest
from io import StringIO
from pathlib import PurePath
from typing import cast

from report_new_linter_errors import (
    DiffLine,
    EXIT_CODE_NO_SNAPSHOT_FILE,
    EXIT_CODE_USAGE,
    GitDiffHunk,
    SnapshotEntry,
    Sys,
    adjust_line_numbers,
    main,
    parse_git_diff_hunks,
    parse_snapshot_lines,
)

stubbed_git = "stubbed-git.bat" if sys.platform == "win32" else "stubbed-git.py"
env_override = {
    "REPORT_NEW_LINTER_ERROR_PATH": os.path.join(os.path.dirname(__file__), "test-tmp"),
    "REPORT_NEW_LINTER_ERROR_GIT_COMMAND": os.path.join(
        os.path.dirname(__file__), "example", stubbed_git
    ),
}
env = os.environ | env_override

profile_name = "default"
profile_root = os.path.join(env["REPORT_NEW_LINTER_ERROR_PATH"], profile_name)
snapshot_path = os.path.join(profile_root, "snapshot")
commit_path = os.path.join(profile_root, "commit")
command_path = os.path.join(profile_root, "command.json")


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
        if os.path.exists(profile_root):
            for name in os.listdir(profile_root):
                os.remove(os.path.join(profile_root, name))
        else:
            os.makedirs(profile_root)

    def _py_linter_cmd(self) -> list[str]:
        # A tiny linter shim controlled by LINTER_LINES.
        program = (
            "import os; "
            "lines=os.environ.get('LINTER_LINES','').splitlines(); "
            "[print(l) for l in lines if l!='']"
        )
        return [sys.executable, "-c", program]

    def test_run_before_snapshot(self):
        test_sys = TestSys(["report_new_linter_errors.py", "run", profile_name])
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(EXIT_CODE_NO_SNAPSHOT_FILE, cm.exception.code)

    def test_snapshot_creates_snapshot_commit_and_command(self):
        test_env = env | {
            "STUB_GIT_HEAD_COMMIT": "deadbeef",
            "LINTER_LINES": "file1.py:1: error: a\nfile2.py:2: error: b\n",
        }
        test_sys = TestSys(
            [
                "report_new_linter_errors.py",
                "snapshot",
                profile_name,
                *self._py_linter_cmd(),
                "--",
                "file1.py",
                "file2.py",
            ]
        )
        main(test_env, cast(Sys, test_sys))
        self.assertTrue(os.path.exists(snapshot_path))
        self.assertTrue(os.path.exists(commit_path))
        self.assertTrue(os.path.exists(command_path))
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                ["file1.py:1: error: a", "file2.py:2: error: b"],
                list(map(str.rstrip, snapshot.readlines())),
            )
        with open(commit_path) as f:
            self.assertEqual("deadbeef", f.read().strip())

    def test_snapshot_without_separator_is_allowed(self):
        test_env = env | {
            "STUB_GIT_HEAD_COMMIT": "deadbeef",
            "LINTER_LINES": "file1.py:1: error: a\n",
        }
        test_sys = TestSys(
            [
                "report_new_linter_errors.py",
                "snapshot",
                profile_name,
                *self._py_linter_cmd(),
            ]
        )
        main(test_env, cast(Sys, test_sys))
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                ["file1.py:1: error: a"],
                list(map(str.rstrip, snapshot.readlines())),
            )

    def test_run_with_new_errors_does_not_update_snapshot(self):
        # First create snapshot.
        test_env1 = env | {
            "STUB_GIT_HEAD_COMMIT": "deadbeef",
            "LINTER_LINES": "file1.py:1: error: a\n",
        }
        test_sys1 = TestSys(
            [
                "report_new_linter_errors.py",
                "snapshot",
                profile_name,
                *self._py_linter_cmd(),
                "--",
                "file1.py",
            ]
        )
        main(test_env1, cast(Sys, test_sys1))
        with open(snapshot_path) as f:
            snapshot_before = f.read()

        # Now run with a new error while file1.py is "changed".
        test_env2 = env | {
            "STUB_GIT_CHANGED_FILES": "file1.py\n",
            "STUB_GIT_UNIFIED_DIFF": "",
            "LINTER_LINES": "file1.py:1: error: a\nfile1.py:3: error: NEW\n",
        }
        test_sys2 = TestSys(["report_new_linter_errors.py", "run", profile_name])
        with self.assertRaises(SystemExit) as cm:
            main(test_env2, cast(Sys, test_sys2))
        self.assertEqual(1, cm.exception.code)
        self.assertIn("file1.py:3: error: NEW", test_sys2.stderr.getvalue())
        with open(snapshot_path) as f:
            self.assertEqual(
                snapshot_before,
                f.read(),
                "snapshot must not change on run",
            )

    def test_run_with_no_new_errors_exits_0_and_snapshot_unchanged(self):
        test_env1 = env | {
            "STUB_GIT_HEAD_COMMIT": "deadbeef",
            "LINTER_LINES": "file1.py:1: error: a\n",
        }
        test_sys1 = TestSys(
            [
                "report_new_linter_errors.py",
                "snapshot",
                profile_name,
                *self._py_linter_cmd(),
                "--",
                "file1.py",
            ]
        )
        main(test_env1, cast(Sys, test_sys1))
        with open(snapshot_path) as f:
            snapshot_before = f.read()

        test_env2 = env | {
            "STUB_GIT_CHANGED_FILES": "file1.py\n",
            "STUB_GIT_UNIFIED_DIFF": "",
            "LINTER_LINES": "file1.py:1: error: a\n",
        }
        test_sys2 = TestSys(["report_new_linter_errors.py", "run", profile_name])
        main(test_env2, cast(Sys, test_sys2))
        self.assertEqual("", test_sys2.stderr.getvalue())
        with open(snapshot_path) as f:
            self.assertEqual(
                snapshot_before,
                f.read(),
                "snapshot must not change on run",
            )

    def test_run_when_no_changed_files_exits_0(self):
        test_env1 = env | {
            "STUB_GIT_HEAD_COMMIT": "deadbeef",
            "LINTER_LINES": "file1.py:1: error: a\n",
        }
        test_sys1 = TestSys(
            [
                "report_new_linter_errors.py",
                "snapshot",
                profile_name,
                *self._py_linter_cmd(),
                "--",
                "file1.py",
            ]
        )
        main(test_env1, cast(Sys, test_sys1))
        with open(snapshot_path) as f:
            snapshot_before = f.read()

        test_env2 = env | {
            "STUB_GIT_CHANGED_FILES": "",
            "STUB_GIT_UNIFIED_DIFF": "",
            "LINTER_LINES": "file1.py:1: error: a\n",
        }
        test_sys2 = TestSys(["report_new_linter_errors.py", "run", profile_name])
        main(test_env2, cast(Sys, test_sys2))
        with open(snapshot_path) as f:
            self.assertEqual(snapshot_before, f.read())

    def test_usage_error_when_missing_args(self):
        test_sys = TestSys(["report_new_linter_errors.py"])
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(EXIT_CODE_USAGE, cm.exception.code)


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

    def test_adjust_line_numbers_with_line_0(self):
        # This tests the fix for the IndexError when a diff hunk starts at line 0
        diff = [
            "--- a/file.py\n",
            "+++ b/file.py\n",
            "@@ -0,0 +1,1 @@\n",
            "+new line\n",
        ]
        snapshot = [
            "file.py:10: error: msg\n",
        ]
        actual = adjust_line_numbers(iter(diff), iter(snapshot))
        self.assertEqual(
            ["file.py:10: error: msg"],
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

    def test_parse_git_diff_hunks_with_single_line_hunk(self):
        diff = [
            "--- a/file.txt\n",
            "+++ b/file.txt\n",
            "@@ -1 +1 @@\n",
            "-old\n",
            "+new\n",
        ]
        actual = list(parse_git_diff_hunks(iter(diff)))
        expected = [
            GitDiffHunk(
                path_minus=PurePath("file.txt"),
                path_plus=PurePath("file.txt"),
                initial_line_number=1,
                diffs=[
                    DiffLine(line_type="-", content="old"),
                    DiffLine(line_type="+", content="new"),
                ],
            )
        ]
        self.assertEqual(actual, expected)


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
