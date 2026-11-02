@echo off
title Biblioteca Offline - mapa do Brasil para o app
cd /d "%~dp0"
echo.
echo   Baixando o mapa do Brasil para ver DENTRO do app (tela "Mapa"), ~5,7 GB.
echo   Pode fechar a janela; rodando de novo, ele continua de onde parou.
echo   Para um mapa menor (~2,9 GB, sem predios): BAIXAR_MAPA_PC.bat --zoom 14
echo.
"..\Programas\Python\python.exe" -I -X utf8 baixar_mapa_pc.py %*
echo.
pause
