@echo off
call "%~dp0..\..\run_python.cmd" scripts/run_idc_best_skill_variants.py --game monster_shoot --models claude-opus-4-6 --no-skill %*
exit /b %ERRORLEVEL%
