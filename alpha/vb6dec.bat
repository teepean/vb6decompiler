@echo off
setlocal
rem How this was started: from Explorer, run_vb6dec.py keeps the window open to read it.
set "VB6DEC_STARTED=%CMDCMDLINE%"
"%~dp0python\python.exe" "%~dp0app\run_vb6dec.py" %*
exit /b %ERRORLEVEL%
