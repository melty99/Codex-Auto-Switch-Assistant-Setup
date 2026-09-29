@echo off
setlocal
set "guardPython=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%guardPython%" set "guardPython=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%guardPython%" (
    for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not exist "%guardPython%" set "guardPython=%%P"
)
if not exist "%guardPython%" (
    for /f "delims=" %%P in ('where python.exe 2^>nul') do if not exist "%guardPython%" set "guardPython=%%P"
)
if not exist "%guardPython%" goto missing
if not exist "%~dp0settings_launcher.py" goto missing
start "" "%guardPython%" "%~dp0settings_launcher.py"
if errorlevel 1 goto failed
exit /b 0
:missing
echo Codex Auto Switch Assistant: Python 3.11+ with tkinter is required.
echo Install Python, enable its PATH option, then open settings and use Quick setup.
:failed
echo Could not open settings. Please keep this message for troubleshooting.
pause
exit /b 1
