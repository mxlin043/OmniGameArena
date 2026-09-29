@echo off
call "%~dp0..\..\run_python.cmd" scripts/run_idc.py --config configs/vlm/idc/obstacle_run_3d.yaml %*
exit /b %ERRORLEVEL%
