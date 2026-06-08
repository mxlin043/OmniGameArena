@echo off
call "%~dp0run_one.cmd" monster_shoot %*
exit /b %ERRORLEVEL%
