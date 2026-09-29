@echo off
call "%~dp0..\..\run_python.cmd" scripts/run_idc_best_skill_variants.py --game solo_craft --models claude-opus-4-6 %*
exit /b %ERRORLEVEL%
