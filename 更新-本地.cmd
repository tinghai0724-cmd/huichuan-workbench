@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo == 本地更新：重启工作台 ==
pwsh -NoProfile -ExecutionPolicy Bypass -File "tools\local-restart.ps1" %*
if errorlevel 1 pause
