@echo off
REM Windows Installation Script for CortexHarness
REM Run this script as Administrator

echo === CortexHarness Windows Installation ===

REM Check Python version
echo Checking Python version...
python --version
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    pause
    exit /b 1
)

REM Check CUDA
echo.
echo Checking NVIDIA GPU/CUDA...
nvidia-smi
if errorlevel 1 (
    echo No NVIDIA GPU detected - will install CPU-only PyTorch
    set USE_CUDA=0
) else (
    echo NVIDIA GPU detected - will install CUDA-enabled PyTorch
    set USE_CUDA=1
)

REM Create virtual environment
echo.
echo Creating virtual environment...
python -m venv .venv
call .venv\Scripts\activate.bat

REM Install basic dependencies
echo.
echo Installing basic dependencies...
python -m pip install --upgrade pip
pip install -e .
pip install -r requirements.txt

REM Install PyTorch
echo.
echo Installing PyTorch...
if %USE_CUDA%==1 (
    echo Installing CUDA-enabled PyTorch...
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
) else (
    echo Installing CPU-only PyTorch...
    pip install torch torchvision torchaudio
)

REM Install code-tiny dependencies
echo.
echo Installing code-tiny dependencies...
pip install -r code-tiny\requirements.txt

REM Fix transformers compatibility
echo.
echo Fixing transformers compatibility...
pip install "transformers<5.0"

REM Create global CLI
echo.
echo Creating global CLI command...
set CORTEX_PATH=C:\ai\cortex-harness

REM Create batch wrapper
echo @echo off > C:\Users\%USERNAME%\dev-global.bat
echo set CORTEX_HARNESS_DIR=%CORTEX_PATH% >> C:\Users\%USERNAME%\dev-global.bat
echo set PYTHON_EXE=%%CORTEX_HARNESS_DIR%%\.venv\Scripts\python.exe >> C:\Users\%USERNAME%\dev-global.bat
echo set DEV_MODULE=%%CORTEX_HARNESS_DIR%%\cortex_harness\dev.py >> C:\Users\%USERNAME%\dev-global.bat
echo "%%PYTHON_EXE%%" "%%DEV_MODULE%%" %%* >> C:\Users\%USERNAME%\dev-global.bat

echo Created wrapper: C:\Users\%USERNAME%\dev-global.bat
echo Add C:\Users\%USERNAME% to your PATH or use the full path to run dev commands

REM Test installation
echo.
echo Testing installation...
python -c "import torch; print('PyTorch version:', torch.__version__); print('CUDA available:', torch.cuda.is_available())"

echo.
echo === Installation Complete ===
echo You can now use 'C:\Users\%USERNAME%\dev-global.bat --help' to test the CLI
echo For other projects, run: pip install -e %CORTEX_PATH%
pause