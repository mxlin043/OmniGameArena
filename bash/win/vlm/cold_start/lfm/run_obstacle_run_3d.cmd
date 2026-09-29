@echo off
call "%~dp0..\..\..\run_python.cmd" scripts/run_cold_start.py --game obstacle_run_3d --clock lfm --models claude-opus-4-6 %*
exit /b %ERRORLEVEL%
