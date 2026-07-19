#!/usr/bin/env python3

"""A tiny stub for `git` used by unit tests.

Supported commands:
- rev-parse HEAD
- diff --name-only <commit>..HEAD
- diff --unified <commit>..HEAD

Outputs are driven by env vars so tests can control behavior:
- STUB_GIT_HEAD_COMMIT
- STUB_GIT_CHANGED_FILES (newline-separated)
- STUB_GIT_UNIFIED_DIFF (full diff output)
"""

import os
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0

    if argv[1:3] == ["rev-parse", "HEAD"]:
        sys.stdout.write(os.environ.get("STUB_GIT_HEAD_COMMIT", "deadbeef") + "\n")
        return 0

    # argv: <stubbed-git> diff --name-only <commit>..HEAD
    if len(argv) >= 4 and argv[1:3] == ["diff", "--name-only"]:
        sys.stdout.write(os.environ.get("STUB_GIT_CHANGED_FILES", ""))
        return 0

    # argv: <stubbed-git> diff --unified <commit>..HEAD
    if len(argv) >= 4 and argv[1:3] == ["diff", "--unified"]:
        sys.stdout.write(os.environ.get("STUB_GIT_UNIFIED_DIFF", ""))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
