@echo off
REM Arranca el bot de presupuestos (loop de 8 a 19 h). Cerrar con Ctrl+C, no con la X:
REM asi termina el job en curso y hace logout de GBP.
cd /d "%~dp0"
".venv\Scripts\python.exe" run.py %*
pause
