@rem Very small stub for git used by unit tests.
@rem The Windows path is kept for parity, but unit tests run on Linux in CI.

@echo off
python .\stubbed-git.py %*
