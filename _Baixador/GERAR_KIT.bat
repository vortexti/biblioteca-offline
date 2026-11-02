@echo off
chcp 65001 >nul
title Biblioteca Offline - gerar o kit de instalacao
cd /d "%~dp0"
echo.
echo   Gera o KIT (zip pequeno) que reinstala esta biblioteca inteira em outro HD.
echo   Leva uns 20 minutos (le a lista de todos os arquivos). O zip fica na raiz do SSD.
echo.
"..\Programas\Python\python.exe" -I -X utf8 instalador\gerar_kit.py
echo.
pause
