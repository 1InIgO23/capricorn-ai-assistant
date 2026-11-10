@echo off
rem Starts Capri without a console window.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" main.py
) else (
  start "" pythonw main.py
)
