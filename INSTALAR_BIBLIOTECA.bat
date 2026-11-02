@echo off
chcp 65001 >nul
title Biblioteca Offline - instalacao
cd /d "%~dp0"
if not exist "Programas\Python\python.exe" (
  echo.
  echo   Nao achei Programas\Python\python.exe.
  echo   Descompacte o kit INTEIRO na pasta de destino ^(de preferencia a raiz de um HD/SSD vazio^)
  echo   e rode este arquivo de dentro dela.
  echo.
  pause
  exit /b 1
)
"Programas\Python\python.exe" -I -X utf8 "_Baixador\instalador\instalar.py" %*
echo.
pause
