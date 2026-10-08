@echo off
rem Install everything RIBBON needs on this Windows PC (run once).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\setup.ps1"
pause
