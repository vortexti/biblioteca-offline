@echo off
title Biblioteca Offline - baixando a biblioteca do conhecimento
cd /d "%~dp0"
if not exist "..\Programas\Python\python.exe" (
  echo Falta o Python em Programas\Python. Rode BAIXAR_BIBLIOTECA.bat primeiro.
  pause
  exit /b 1
)
echo.
echo   Baixando a biblioteca do conhecimento (pode levar 1 a 2 dias).
echo   Pode fechar a janela; rodando de novo, ele continua de onde parou.
echo.
"..\Programas\Python\python.exe" -I -X utf8 baixar_conhecimento.py %*
echo.
pause
