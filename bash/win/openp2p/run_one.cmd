@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Run one standard OpenP2P solo benchmark config from the repo root.
REM Usage:
REM   bash\win\openp2p\run_one.cmd obstacle_run_3d [extra run_benchmark args...]

if "%~1"=="" goto Usage

set "GAME=%~1"
cd /d "%~dp0..\..\.."

if not defined IP set "IP=127.0.0.1"
if not defined PORT set "PORT=12345"
if not defined EPISODES set "EPISODES=5"

set "CONFIG=configs\openp2p\%GAME%.yaml"
if not exist "%CONFIG%" (
  echo [error] Config not found: %CONFIG%
  echo.
  goto Usage
)

set "EXTRA_ARGS="
shift
:CollectArgs
if "%~1"=="" goto Run
set "EXTRA_ARGS=!EXTRA_ARGS! "%~1""
shift
goto CollectArgs

:Run
echo.
echo ===== OpenP2P: %GAME% =====
echo config=%CONFIG%
echo host=%IP% port=%PORT%
echo standard=%EPISODES% episode(s), live, log, gameplay video recording
python scripts\run_benchmark.py ^
  --config "%CONFIG%" ^
  --host "%IP%" ^
  --port "%PORT%" ^
  --episodes "%EPISODES%" ^
  --live ^
  --log ^
  --record-video ^
  --video-with-thinking^
  !EXTRA_ARGS!
exit /b %ERRORLEVEL%

:Usage
echo Usage:
echo   bash\win\openp2p\run_one.cmd GAME [extra run_benchmark args...]
echo.
echo Games:
echo   obstacle_run_3d
echo   obstacle_run_2d
echo   last_stand
echo   monster_shoot
echo   cue_chase
echo   scene_escape
echo   solo_craft
exit /b 2
