@echo off
call "%~dp0..\..\..\run_python.cmd" scripts/run_cold_start.py --game sky_duel --clock lcm --models claude-opus-4-6 --opponents gpt-5.5 %*
exit /b %ERRORLEVEL%
