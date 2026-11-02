@echo off
title Biblioteca Offline - acervo em portugues
cd /d "%~dp0"
echo.
echo   Baixando livros e manuais em portugues (SciELO Livros + Ministerio da Saude).
echo   Pode fechar a janela; rodando de novo, ele continua de onde parou.
echo.
"..\Programas\Python\python.exe" -I -X utf8 baixar_pt.py %*
echo.
pause
