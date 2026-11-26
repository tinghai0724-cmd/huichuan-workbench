@echo off
rem 慧川工作台启动器：被计划任务/快捷方式调用，日志追加到 comfy_web\data
setlocal
cd /d "%~dp0..\comfy_web"
if not exist "data" mkdir "data"
python -u server.py --port 8787 1>>"data\server.out.log" 2>>"data\server.err.log"
