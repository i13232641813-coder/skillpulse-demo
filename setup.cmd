@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"

python --version >nul 2>&1
if errorlevel 1 (
  echo [SkillPulse] 未检测到 Python。请安装 Python 3.10 或更高版本并加入 PATH。
  pause
  exit /b 1
)

python --version
python -c "import sys; raise SystemExit(0 if sys.version_info.__ge__((3, 10)) else 1)"
if errorlevel 1 (
  echo [SkillPulse] 需要 Python 3.10 或更高版本。
  pause
  exit /b 1
)
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [SkillPulse] 正在创建项目专用虚拟环境...
  python -m venv "%~dp0.venv"
  if errorlevel 1 (
    echo [SkillPulse] 创建虚拟环境失败。
    pause
    exit /b 1
  )
)

echo [SkillPulse] 正在安装 backend\requirements.txt 中的依赖...
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input --no-compile --only-binary=:all: -r "%~dp0backend\requirements.txt"
if errorlevel 1 (
  echo [SkillPulse] 依赖安装失败。请检查网络或 pip 镜像配置后重试。
  pause
  exit /b 1
)

echo [SkillPulse] 环境准备完成。下一步运行 start.cmd。
pause
