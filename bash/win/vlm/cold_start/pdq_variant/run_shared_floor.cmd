@echo off
setlocal EnableExtensions EnableDelayedExpansion
@REM shared_floor (coop) cold-start -- pdq_variant
@REM config: configs\vlm\cold_start\coop\shared_floor\variant_pdq.yaml
@REM (clock mode is set by which subfolder this script lives in: pdq / lcrt / pdq_variant)

@REM ========================= TWEAK HERE =========================
set "EPISODES=5"

@REM How EPISODES is counted (per model self-play pair):
@REM   fresh = always run EPISODES NEW matches, ignore what's already there
@REM   topup = count finished matches already on disk, run only the missing ones
set "COUNT=fresh"

@REM Record the right-side reason/action panel in episode.mp4.
@REM   1 = on, 0 = plain gameplay video
set "VIDEO_WITH_THINKING=1"

@REM Coop self-play models -- ONE PER LINE. Default: only claude-opus-4-6.
@REM Each selected model is run as BOTH player1 and player2.
@REM Remove the "REM " in front of a line to also run that model.
set "MODELS="
set "MODELS=!MODELS! claude-opus-4-6"
@REM set "MODELS=!MODELS! claude-opus-4-7"
@REM set "MODELS=!MODELS! gpt-5.5"
@REM set "MODELS=!MODELS! gemini-3.1-pro-preview"

@REM ==============================================================

set "VIDEO_PANEL_ARGS="
if not "%VIDEO_WITH_THINKING%"=="0" set "VIDEO_PANEL_ARGS=--video-with-thinking"

set "GAME=shared_floor"
set "OUTROOT=runs\pdq_variant"
set "CONFIG=configs\vlm\cold_start\coop\%GAME%\variant_pdq.yaml"
cd /d "%~dp0..\..\..\..\.."
if not exist "%CONFIG%" ( echo [error] config not found: %CONFIG% & exit /b 2 )

echo.
echo ===== %GAME% / coop / pdq_variant target=%EPISODES% count=%COUNT% =====
@REM Coop requires a same-model pair, so %%M is passed as both players.
@REM Ports come from the config players: list (player1 -> 12345, player2 -> 12346).
for %%M in (!MODELS!) do (
  set "CELL=%OUTROOT%\%GAME%\player1-%%M_vs_player2-%%M"
  set /a HAVE=0
  if exist "!CELL!\" for /d %%E in ("!CELL!\*") do if exist "%%~fE\match_result.json" set /a HAVE+=1
  if /I "%COUNT%"=="topup" ( set /a TORUN=EPISODES-HAVE ) else ( set /a TORUN=EPISODES )
  echo.
  if !TORUN! GTR 0 (
    echo --- %%M self-play : have !HAVE!, running !TORUN! more ---
    python scripts\run_benchmark.py --config "%CONFIG%" --players %%M %%M --episodes !TORUN! --live --log --record-video %VIDEO_PANEL_ARGS% %*
  ) else (
    echo --- %%M self-play : already has !HAVE! of %EPISODES% -- skip ---
  )
)
exit /b 0
