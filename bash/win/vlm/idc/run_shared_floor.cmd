@echo off
setlocal EnableExtensions EnableDelayedExpansion
@REM shared_floor IDC (coop self-cooperation)
@REM config: configs\vlm\idc\shared_floor.yaml

@REM ========================= TWEAK HERE =========================
set "MODEL=claude-opus-4-6"
@REM set "MODEL=claude-opus-4-7"
@REM set "MODEL=gpt-5.5"
@REM set "MODEL=gemini-3.1-pro-preview"

@REM Empty = use MODEL as the reflector model.
set "REFLECTOR_MODEL="

@REM Coop IDC uses MODEL for both players and one shared skill per round.
@REM PORT is player_1; player_2 automatically uses PORT + 1.
set "IP=127.0.0.1"
set "PORT=12345"
set "ROUNDS=10"
set "EPISODES_PER_ROUND=5"
set "PDQ_ROOT=runs\pdq"
set "OUTPUT_ROOT=runs\idc"

@REM Set this to an existing run directory to resume instead of starting fresh.
@REM Example: set "RESUME=runs\idc\shared_floor\claude-opus-4-7\20260530_120000"
set "RESUME="

set "LIVE=1"
set "LOG_VLM=0"
set "API_DEBUG=0"
set "VERBOSE=0"
@REM ==============================================================

set "LIVE_ARGS="
if not "%LIVE%"=="0" set "LIVE_ARGS=--live"

set "LOG_ARGS="
if not "%LOG_VLM%"=="0" set "LOG_ARGS=--log-vlm"

set "API_DEBUG_ARGS="
if not "%API_DEBUG%"=="0" set "API_DEBUG_ARGS=--api-debug"

set "VERBOSE_ARGS="
if not "%VERBOSE%"=="0" set "VERBOSE_ARGS=--verbose"

set "REFLECTOR_ARGS="
if not "%REFLECTOR_MODEL%"=="" set "REFLECTOR_ARGS=--reflector-model %REFLECTOR_MODEL%"

set /a PORT2=PORT+1

set "GAME=shared_floor"
set "CONFIG=configs\vlm\idc\%GAME%.yaml"
cd /d "%~dp0..\..\..\.."
if not exist "%CONFIG%" ( echo [error] config not found: %CONFIG% & exit /b 2 )

if not "%RESUME%"=="" (
  if not exist "%RESUME%\idc_config.json" ( echo [error] resume idc_config not found: %RESUME%\idc_config.json & exit /b 2 )
  echo ===== %GAME% / IDC resume / %IP%:%PORT% and %IP%:%PORT2% =====
  echo resume=%RESUME%
  python scripts\run_idc.py --resume "%RESUME%" --host "%IP%" --port "%PORT%" %LIVE_ARGS% %LOG_ARGS% %API_DEBUG_ARGS% %VERBOSE_ARGS% %REFLECTOR_ARGS% %*
) else (
  echo ===== %GAME% / IDC coop / %MODEL% self-coop / %IP%:%PORT% and %IP%:%PORT2% rounds=%ROUNDS% eps_per_round=%EPISODES_PER_ROUND% =====
  python scripts\run_idc.py --config "%CONFIG%" --model "%MODEL%" --host "%IP%" --port "%PORT%" --rounds "%ROUNDS%" --episodes-per-round "%EPISODES_PER_ROUND%" --pdq-root "%PDQ_ROOT%" --output-root "%OUTPUT_ROOT%" %LIVE_ARGS% %LOG_ARGS% %API_DEBUG_ARGS% %VERBOSE_ARGS% %REFLECTOR_ARGS% %*
)
exit /b !ERRORLEVEL!
