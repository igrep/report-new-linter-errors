@rem Very small stub for git used by unit tests.
@rem The Windows path is kept for parity, but unit tests run on Linux in CI.

@echo off
setlocal enabledelayedexpansion

if "%1"=="rev-parse" if "%2"=="HEAD" (
  if defined STUB_GIT_HEAD_COMMIT (
    echo %STUB_GIT_HEAD_COMMIT%
  ) else (
    echo deadbeef
  )
  exit /b 0
)

if "%1"=="diff" if "%2"=="--name-only" (
  if defined STUB_GIT_CHANGED_FILES (
    echo %STUB_GIT_CHANGED_FILES%
  )
  exit /b 0
)

if "%1"=="diff" if "%2"=="--unified" (
  if defined STUB_GIT_UNIFIED_DIFF (
    echo %STUB_GIT_UNIFIED_DIFF%
  )
  exit /b 0
)

exit /b 0
