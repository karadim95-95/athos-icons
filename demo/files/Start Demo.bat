@echo off
rem Starts the Athos Icons demo. Keep this window open while you use the shop.
cd /d "%~dp0"
title Athos Icons demo
if not exist "python\python.exe" (
  echo.
  echo   Please unzip the whole folder first ^(right-click the zip, "Extract All"^),
  echo   then open the extracted folder and double-click "Start Demo" there.
  echo.
  pause
  exit /b 1
)
"python\python.exe" "app\demo\launch.py"
echo.
echo The demo has stopped.
pause
