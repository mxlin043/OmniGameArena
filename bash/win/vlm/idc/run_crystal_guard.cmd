@echo off
call "%~dp0..\..\run_python.cmd" scripts/run_idc.py --config configs/vlm/idc/crystal_guard.yaml %*
exit /b %ERRORLEVEL%
