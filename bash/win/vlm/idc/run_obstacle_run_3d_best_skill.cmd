@echo off
call "%~dp0..\..\run_python.cmd" scripts/run_idc_best_skill_variants.py --game obstacle_run_3d --models claude-opus-4-6 %*
exit /b %ERRORLEVEL%
