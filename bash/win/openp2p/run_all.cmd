@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Run every OpenP2P game cmd in this directory.
REM Extra args are passed through to each game script.

set "FAILURES="
set "COUNT=0"

for %%F in ("%~dp0run_*.cmd") do (
  set "SCRIPT=%%~nxF"
  if /I not "!SCRIPT!"=="run_one.cmd" if /I not "!SCRIPT!"=="run_all.cmd" (
    set /a COUNT+=1
    echo.
    echo ===== Running !SCRIPT! =====
    call "%%~fF" %*
    if errorlevel 1 (
      echo [error] !SCRIPT! failed with exit code !ERRORLEVEL!
      if defined FAILURES (
        set "FAILURES=!FAILURES!, !SCRIPT!"
      ) else (
        set "FAILURES=!SCRIPT!"
      )
    )
  )
)

echo.
echo ===== OpenP2P run_all complete: !COUNT! script(s) =====
if defined FAILURES (
  echo Failed: !FAILURES!
  exit /b 1
)

echo All scripts completed successfully.
exit /b 0
