@echo off
call "%~dp0run_one.cmd" solo_craft %*
exit /b %ERRORLEVEL%
