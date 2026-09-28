@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "APP_DIR=%~dp0backend"
set "PORT=8000"

if not exist "%PYTHON%" (
  echo [SkillPulse] 项目虚拟环境不存在，请先运行 setup.cmd。
  pause
  exit /b 1
)

"%PYTHON%" -c "import fastapi, uvicorn, pydantic, multipart" >nul 2>&1
if errorlevel 1 (
  echo [SkillPulse] 依赖未安装完整，请先运行 setup.cmd。
  pause
  exit /b 1
)

netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
  echo [SkillPulse] 127.0.0.1:8000 已被占用。请先关闭旧的 SkillPulse Backend 窗口，再启动本版本。
  echo [SkillPulse] 为避免打开旧页面，本启动器不会自动改用 8001。
  pause
  exit /b 1
)

echo [SkillPulse] 正在启动本地 API（不读取 backend\.env）。
start "SkillPulse Backend" cmd /k ""%PYTHON%" -m uvicorn app:app --host 127.0.0.1 --port %PORT% --app-dir "%APP_DIR%""

set /a ATTEMPTS=0
:wait
timeout /t 1 >nul
curl -fsS "http://127.0.0.1:%PORT%/api/auth/status" >nul 2>&1
if not errorlevel 1 goto ready
set /a ATTEMPTS+=1
if %ATTEMPTS% GEQ 30 (
  echo [SkillPulse] 后端未能在 30 秒内就绪，请查看 SkillPulse Backend 窗口。
  pause
  exit /b 1
)
goto wait

:ready
echo [SkillPulse] 已启动：
echo   Skill 市场与流程编排：http://127.0.0.1:%PORT%/frontend/lego/index.html
echo   健康检查：http://127.0.0.1:%PORT%/frontend/index.html
echo   API 文档：http://127.0.0.1:%PORT%/docs
start "" "http://127.0.0.1:%PORT%/frontend/lego/index.html"
pause
