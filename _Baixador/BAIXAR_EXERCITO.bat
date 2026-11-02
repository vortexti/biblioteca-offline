@echo off
cd /d "%~dp0"
"..\Programas\Python\python.exe" -I -X utf8 baixar_exercito.py %*
echo.
pause
