@echo off
rem Start the RIBBON server and open it in the browser. Add -Lan to accept other computers.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start.ps1" %*
pause
