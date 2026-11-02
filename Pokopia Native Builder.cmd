@echo off
rem Pokopia Native Builder (Windows): opens the window (nxs2_gui.py) with Python 3.12 or newer. The python.org installer
rem puts "py" on the PATH; "pyw" runs without a console window.
setlocal
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0nxs2_gui.py" && exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw "%~dp0nxs2_gui.py" && exit /b 0)
where python >nul 2>nul && (start "" python "%~dp0nxs2_gui.py" && exit /b 0)
echo Python 3.12 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.
pause
