@echo off
setlocal EnableExtensions EnableDelayedExpansion
@REM midline_clash (pvp) cold-start -- pdq
@REM config: configs\vlm\cold_start\pvp\midline_clash\vanilla_pdq.yaml
@REM (clock mode is set by which subfolder this script lives in: pdq / lcrt / pdq_variant)

@REM ========================= TWEAK HERE =========================
set "EPISODES=5"

@REM How EPISODES is counted (per pairing):
@REM   fresh = always run EPISODES NEW matches, ignore what's already there
@REM   topup = count finished matches already on disk, run only the missing ones
set "COUNT=fresh"

@REM Record the right-side reason/action panel in episode.mp4.
@REM   1 = on, 0 = plain gameplay video
set "VIDEO_WITH_THINKING=1"

@REM Two-player pairing. P1 = player1, P2 = player2 (1-indexed -- exactly what
@REM shows up in the output dirs player1-.../player2-... and what you think in).
@REM The run loops EVERY P1 x EVERY P2, but skips same-model matchups.
@REM Any model works (even one the YAML never listed).

@REM --- Player 1 models -- ONE PER LINE. (uncomment to add; @REM to drop.) ---
set "P1="
set "P1=!P1! claude-opus-4-6"
@REM set "P1=!P1! gpt-5.5"
@REM set "P1=!P1! gemini-3.1-pro-preview"
@REM set "P1=!P1! Kimi-K2.5"

@REM Qwen models require a self-hosted deployment first.
@REM Deploy the target Qwen model, obtain its host and port, then update the pvp YAML players before uncommenting.
@REM set "P1=!P1! qwen3.5-397b-a17b"

@REM --- Player 2 models -- ONE PER LINE. (uncomment to add; @REM to drop.) ---
set "P2="
set "P2=!P2! claude-opus-4-6"
set "P2=!P2! gpt-5.5"
@REM set "P2=!P2! gemini-3.1-pro-preview"
@REM set "P2=!P2! Kimi-K2.5"

@REM Qwen models require a self-hosted deployment first.
@REM Deploy the target Qwen model, obtain its host and port, then update the pvp YAML players before uncommenting.
@REM set "P2=!P2! qwen3.5-397b-a17b"

@REM ==============================================================

set "VIDEO_PANEL_ARGS="
if not "%VIDEO_WITH_THINKING%"=="0" set "VIDEO_PANEL_ARGS=--video-with-thinking"

set "GAME=midline_clash"
set "OUTROOT=runs\pdq"
set "CONFIG=configs\vlm\cold_start\pvp\%GAME%\vanilla_pdq.yaml"
cd /d "%~dp0..\..\..\..\.."
if not exist "%CONFIG%" ( echo [error] config not found: %CONFIG% & exit /b 2 )

echo.
echo ===== %GAME% / pvp / pdq target=%EPISODES% count=%COUNT% =====
@REM %%A = this round's player1 model, %%B = player2 model. They map to
@REM run_benchmark's positional --players (player1 first, player2 second);
@REM you never touch python's 0-indexing here. Ports come from the config
@REM players: list (player1 -> 12345, player2 -> 12346).
for %%A in (!P1!) do for %%B in (!P2!) do (
  echo.
  if /I "%%A"=="%%B" (
    echo --- player1=%%A vs player2=%%B : same model -- skip ---
  ) else (
    set "CELL=%OUTROOT%\%GAME%\player1-%%A_vs_player2-%%B"
    set /a HAVE=0
    if exist "!CELL!\" for /d %%E in ("!CELL!\*") do if exist "%%~fE\player_1\result.json" set /a HAVE+=1
    if /I "%COUNT%"=="topup" ( set /a TORUN=EPISODES-HAVE ) else ( set /a TORUN=EPISODES )
    if !TORUN! GTR 0 (
      echo --- player1=%%A vs player2=%%B : have !HAVE!, running !TORUN! more ---
      python scripts\run_benchmark.py --config "%CONFIG%" --players %%A %%B --episodes !TORUN! --live --log --record-video %VIDEO_PANEL_ARGS% %*
    ) else (
      echo --- player1=%%A vs player2=%%B : already has !HAVE! of %EPISODES% -- skip ---
    )
  )
)
exit /b 0
