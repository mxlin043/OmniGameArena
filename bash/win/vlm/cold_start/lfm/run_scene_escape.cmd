@echo off
call "%~dp0..\..\..\run_python.cmd" scripts/run_cold_start.py --game scene_escape --clock lfm --models claude-opus-4-6 %*
exit /b %ERRORLEVEL%
