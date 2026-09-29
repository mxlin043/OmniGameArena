@echo off
call "%~dp0..\..\run_python.cmd" scripts/run_idc.py --config configs/vlm/idc/cue_chase.yaml %*
exit /b %ERRORLEVEL%
