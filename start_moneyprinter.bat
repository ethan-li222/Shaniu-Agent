@echo off
cd /d "%~dp0"
if not exist ".venv-moneyprinter\Scripts\python.exe" (
    echo MoneyPrinter runtime is missing. See the installation steps in README.md.
    pause
    exit /b 1
)
".venv-moneyprinter\Scripts\python.exe" scripts\start_moneyprinter.py
if errorlevel 1 pause
