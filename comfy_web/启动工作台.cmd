@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 ComfyUI 工作台...
python server.py --port 8787 --open
pause
