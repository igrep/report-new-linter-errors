import os
import unittest
import sys
from io import StringIO
from typing import cast

from report_new_linter_errors import main, Sys, EXIT_CODE_NO_SNAPSHOT_FILE
import example_linter

env = os.environ | {'REPORT_NEW_LINTER_ERROR_PATH': os.path.join(
    os.path.dirname(__file__), 'test-tmp')}

snapshot_path = os.path.join(env['REPORT_NEW_LINTER_ERROR_PATH'], 'snapshot')


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


class ReportNewLinterErrorsTestCase(unittest.TestCase):

    def setUp(self):
        try:
            os.remove(snapshot_path)
        except FileNotFoundError:
            pass

        test_sys = TestSys(
            ['report_new_linter_errors.py', 'python', 'example_linter.py',
             'setUp'])
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(cm.exception.code, EXIT_CODE_NO_SNAPSHOT_FILE)
        self.assertEqual(
            test_sys.stderr.getvalue().splitlines()[-2:],
            [
                'Snapshot file not found. Creating a new snapshot file.',
                'Run this command later again to check if new errors are introduced.',
            ],
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                list(map(str.rstrip, snapshot.readlines())),
                example_linter.original_output,
                "snapshot file is created",
            )

    def test_new_errors_found(self):
        test_sys = TestSys(
            ['report_new_linter_errors.py', 'python', 'example_linter.py',
             'new_errors'])
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            test_sys.stderr.getvalue().splitlines()[-1],
            'ERROR: diff command reported that the command may have produced new errors. Fix it or update the snapshot.',
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                list(map(str.rstrip, snapshot.readlines())),
                example_linter.original_output,
                "snapshot file is NOT updated",
            )

    def test_fewer_errors_found(self):
        test_sys = TestSys(
            ['report_new_linter_errors.py', 'python', 'example_linter.py',
             'fewer_errors'])
        main(env, cast(Sys, test_sys))
        self.assertEqual(
            test_sys.stderr.getvalue(),
            '',
            'No error message is printed to stderr',
        )
        self.assertEqual(
            list(map(str.rstrip, test_sys.stdout.getvalue().splitlines()))[-2:],
            [
                'Congratulations! It looks like that you fixed some errors.',
                'Saving the new snapshot.',
            ],
            "stdout contains the message to praise the user",
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                list(map(str.rstrip, snapshot.readlines())),
                [
                    '---original output 1',
                    '   original output 3',
                ],
                "snapshot file is updated with the new output",
            )

    def test_when_no_changes(self):
        test_sys = TestSys(
            ['report_new_linter_errors.py', 'python', 'example_linter.py',
             'setUp'])
        main(env, cast(Sys, test_sys))
        self.assertEqual(
            test_sys.stderr.getvalue(),
            '',
            'No error message is printed to stderr',
        )
        self.assertNotRegex(
            test_sys.stdout.getvalue().rstrip(),
            r'Congratulations! It looks like that you fixed some errors',
            'No message to praise the user is printed to stdout',
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                example_linter.original_output,
                list(map(str.rstrip, snapshot.readlines())),
                "snapshot file is NOT updated",
            )

    def test_when_removed_and_added(self):
        test_sys = TestSys(
            ['report_new_linter_errors.py', 'python', 'example_linter.py',
             'removed_and_added'])
        with self.assertRaises(SystemExit) as cm:
            main(env, cast(Sys, test_sys))
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(
            test_sys.stderr.getvalue().splitlines()[-1],
            'ERROR: diff command reported that the command may have produced new errors. Fix it or update the snapshot.',
        )
        self.assertEqual(
            list(map(str.rstrip, test_sys.stdout.getvalue().splitlines()))[-1],
            'Thank you! It looks like that you fixed some errors. But you also introduced new errors. Fix it!',
            "stdout contains the new output",
        )
        with open(snapshot_path) as snapshot:
            self.assertEqual(
                list(map(str.rstrip, snapshot.readlines())),
                example_linter.original_output,
                "snapshot file is NOT updated",
            )

    if __name__ == '__main__':
        unittest.main()
