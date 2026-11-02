@echo off
title Biblioteca Offline - Embrapa
cd /d "%~dp0"
echo.
echo   Baixando o acervo completo da Embrapa (Infoteca-e).
echo   Pode fechar a janela; rodando de novo, ele continua de onde parou.
echo.
"..\Programas\Python\python.exe" -I -X utf8 baixar_embrapa.py %*
echo.
pause
