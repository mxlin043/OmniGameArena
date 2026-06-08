@echo off
setlocal EnableExtensions EnableDelayedExpansion
@REM last_stand held-out variants with each model's best measured IDC skill.
@REM Run this after the corresponding IDC runs have produced round results.

@REM ========================= TWEAK HERE =========================
set "IP=127.0.0.1"
set "PORT=12345"
set "EPISODES=5"
set "VARIANTS=var1 var2 var3"

@REM Empty = auto-select the best measured IDC round.
@REM Example: set "SKILL_ROUND=5" uses round_05\skill_out.md.
set "SKILL_ROUND="

set "IDC_ROOT=runs\idc"
@REM Empty = auto-select latest run under IDC_ROOT\last_stand\<model>.
@REM Set this to one exact IDC run directory for reproducible variant eval.
@REM Example: set "IDC_RUN=runs\idc\last_stand\claude-opus-4-7\20260530_120000"
set "IDC_RUN="
set "OUTPUT_SUBDIR=unseen_variants"
set "ARM_NAME=best_skill"

@REM Models to evaluate -- ONE PER LINE.
set "MODELS="
set "MODELS=!MODELS! claude-opus-4-6"
@REM set "MODELS=!MODELS! claude-opus-4-7"
@REM set "MODELS=!MODELS! gpt-5.5"
@REM set "MODELS=!MODELS! gemini-3.1-pro-preview"

set "LIVE=1"
set "LOG=1"
set "API_DEBUG=1"
@REM The Python runner records video with the right-side thinking panel when RECORD_VIDEO=1.
set "RECORD_VIDEO=1"
set "FLAT_OUTPUT=0"
set "ALLOW_MISSING=0"
set "DRY_RUN=0"
@REM ==============================================================

set "CONFIG_PATTERN=configs\vlm\cold_start\solo\last_stand\variant_pdq_{variant}.yaml"

set "SKILL_ROUND_ARGS="
if not "%SKILL_ROUND%"=="" set "SKILL_ROUND_ARGS=--skill-round %SKILL_ROUND%"

set "LIVE_ARGS="
if "%LIVE%"=="0" set "LIVE_ARGS=--no-live"

set "LOG_ARGS="
if "%LOG%"=="0" set "LOG_ARGS=--no-log"

set "API_DEBUG_ARGS="
if "%API_DEBUG%"=="0" set "API_DEBUG_ARGS=--no-api-debug"

set "VIDEO_ARGS="
if "%RECORD_VIDEO%"=="0" set "VIDEO_ARGS=--no-video"

set "FLAT_ARGS="
if not "%FLAT_OUTPUT%"=="0" set "FLAT_ARGS=--flat-output"

set "ALLOW_ARGS="
if not "%ALLOW_MISSING%"=="0" set "ALLOW_ARGS=--allow-missing"

set "DRY_RUN_ARGS="
if not "%DRY_RUN%"=="0" set "DRY_RUN_ARGS=--dry-run"

set "GAME=last_stand"
cd /d "%~dp0..\..\..\.."
if not exist "scripts\run_idc_best_skill_variants.py" ( echo [error] runner not found: scripts\run_idc_best_skill_variants.py & exit /b 2 )
if not exist "configs\vlm\cold_start\solo\last_stand\variant_pdq_var1.yaml" ( echo [error] variant config not found for %GAME% & exit /b 2 )

echo ===== %GAME% / IDC best skill variants / %IP%:%PORT% episodes=%EPISODES% variants=%VARIANTS% =====
if not "%IDC_RUN%"=="" (
  echo idc_run=%IDC_RUN%
  python scripts\run_idc_best_skill_variants.py --game "%GAME%" --idc-run "%IDC_RUN%" --config-pattern "%CONFIG_PATTERN%" --variants %VARIANTS% --episodes "%EPISODES%" --host "%IP%" --port "%PORT%" --output-subdir "%OUTPUT_SUBDIR%" --arm-name "%ARM_NAME%" %SKILL_ROUND_ARGS% %LIVE_ARGS% %LOG_ARGS% %API_DEBUG_ARGS% %VIDEO_ARGS% %FLAT_ARGS% %ALLOW_ARGS% %DRY_RUN_ARGS% %*
) else (
  echo idc_root=%IDC_ROOT%
  echo models=%MODELS%
  python scripts\run_idc_best_skill_variants.py --game "%GAME%" --idc-root "%IDC_ROOT%" --config-pattern "%CONFIG_PATTERN%" --models %MODELS% --variants %VARIANTS% --episodes "%EPISODES%" --host "%IP%" --port "%PORT%" --output-subdir "%OUTPUT_SUBDIR%" --arm-name "%ARM_NAME%" %SKILL_ROUND_ARGS% %LIVE_ARGS% %LOG_ARGS% %API_DEBUG_ARGS% %VIDEO_ARGS% %FLAT_ARGS% %ALLOW_ARGS% %DRY_RUN_ARGS% %*
)
exit /b !ERRORLEVEL!
