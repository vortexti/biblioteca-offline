@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "Programas\Python\python.exe" (
  echo.
  echo   Nao encontrei o Python em Programas\Python.
  echo   Rode o _Baixador\BAIXAR_BIBLIOTECA.bat primeiro.
  echo.
  pause
  exit /b 1
)
"Programas\Python\python.exe" -X utf8 "App\main.py" %*
