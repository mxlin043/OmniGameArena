@echo off
setlocal
pushd "%~dp0..\.." || exit /b 1
if not defined PYTHON (
  if exist ".venv\Scripts\python.exe" (set "PYTHON=.venv\Scripts\python.exe") else (set "PYTHON=python")
)
"%PYTHON%" %*
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%
