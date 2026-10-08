@echo off
rem Copy RIBBON and its tools to a USB drive, e.g.: prepare-usb.bat E:\RIBBON
if "%~1"=="" (
  echo Uso: prepare-usb.bat E:\RIBBON
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\prepare-usb.ps1" -Destination "%~1"
pause
