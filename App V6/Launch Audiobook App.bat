@echo off
rem Double-click launcher for the EPUB to Audiobook app (V6).
rem Runs the GUI with pythonw so there is no console window.
cd /d "%~dp0"

set "PYW=pythonw"
where pythonw >nul 2>nul
if not errorlevel 1 goto run

if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" goto run

echo Could not find Python (pythonw).
echo Install Python from https://www.python.org/downloads/ and try again.
pause
exit /b 1

:run
start "" "%PYW%" "epub_to_audiobook_gui_v6.py"
