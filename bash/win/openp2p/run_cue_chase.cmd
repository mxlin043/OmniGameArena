@echo off
call "%~dp0run_one.cmd" cue_chase %*
exit /b %ERRORLEVEL%
