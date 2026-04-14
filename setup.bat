@echo off
REM TokioAI CLI — Windows Setup
REM Requires Python 3.10+ installed and in PATH

echo.
echo  ╔══════════════════════════════════════╗
echo  ║      TokioAI CLI — Windows Setup     ║
echo  ╚══════════════════════════════════════╝
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

python --version 2>&1 | findstr /R "3\.1[0-9]" >nul 2>&1
if %errorlevel% neq 0 (
    python --version 2>&1 | findstr /R "3\.[0-9] " >nul 2>&1
    if %errorlevel% equ 0 (
        echo [WARN] Python 3.10+ recommended. You have:
        python --version
    )
)

echo [1/4] Creating virtual environment...
if exist .venv (
    echo       .venv already exists, skipping
) else (
    python -m venv .venv
)

echo [2/4] Activating venv...
call .venv\Scripts\activate.bat

echo [3/4] Installing dependencies...
pip install --upgrade pip setuptools wheel >nul 2>&1
pip install -e . 2>&1

echo [4/4] Verifying installation...
python -c "from tokio_cli.interactive import main; print('  OK: TokioAI CLI ready!')" 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Installation failed. Check errors above.
    pause
    exit /b 1
)

echo.
echo  ✅ TokioAI CLI installed successfully!
echo.
echo  To use:
echo    .venv\Scripts\activate
echo    tokioai
echo.
pause
