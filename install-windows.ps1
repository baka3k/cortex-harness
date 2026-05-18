# Windows Installation Script for CortexHarness
# Run this script in PowerShell as Administrator

Write-Host "=== CortexHarness Windows Installation ===" -ForegroundColor Green

# Check Python version
Write-Host "Checking Python version..." -ForegroundColor Yellow
python --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Python is not installed or not in PATH" -ForegroundColor Red
    exit 1
}

# Check CUDA
Write-Host "`nChecking NVIDIA GPU/CUDA..." -ForegroundColor Yellow
nvidia-smi
if ($LASTEXITCODE -eq 0) {
    Write-Host "NVIDIA GPU detected - will install CUDA-enabled PyTorch" -ForegroundColor Green
    $use_cuda = $true
} else {
    Write-Host "No NVIDIA GPU detected - will install CPU-only PyTorch" -ForegroundColor Yellow
    $use_cuda = $false
}

# Create virtual environment
Write-Host "`nCreating virtual environment..." -ForegroundColor Yellow
python -m venv .venv
.venv\Scripts\activate

# Install basic dependencies
Write-Host "`nInstalling basic dependencies..." -ForegroundColor Yellow
pip install --upgrade pip
pip install -e .
pip install -r requirements.txt

# Install PyTorch
Write-Host "`nInstalling PyTorch..." -ForegroundColor Yellow
if ($use_cuda) {
    Write-Host "Installing CUDA-enabled PyTorch..." -ForegroundColor Green
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
} else {
    Write-Host "Installing CPU-only PyTorch..." -ForegroundColor Yellow
    pip install torch torchvision torchaudio
}

# Install code-tiny dependencies
Write-Host "`nInstalling code-tiny dependencies..." -ForegroundColor Yellow
pip install -r code-tiny\requirements.txt

# Fix transformers compatibility
Write-Host "`nFixing transformers compatibility..." -ForegroundColor Yellow
pip install "transformers<5.0"

# Create global CLI
Write-Host "`nCreating global CLI command..." -ForegroundColor Yellow
$cortexPath = "C:\ai\cortex-harness"
if (Test-Path "$env:USERPROFILE\scoop\shims") {
    Write-Host "Creating scoop shim..." -ForegroundColor Green
    $shimContent = "path = `"$cortexPath\.venv\Scripts\dev.exe`""
    $shimContent | Out-File -FilePath "$env:USERPROFILE\scoop\shims\dev.shim" -Encoding utf8
    Write-Host "Created scoop shim: dev.exe" -ForegroundColor Green
} else {
    Write-Host "Scoop not found. You can create a PowerShell alias instead:" -ForegroundColor Yellow
    Write-Host "Add to your PowerShell profile:" -ForegroundColor White
    Write-Host 'function dev { & "'"$cortexPath\.venv\Scripts\dev.exe"'" @Args }' -ForegroundColor Cyan
}

# Test installation
Write-Host "`nTesting installation..." -ForegroundColor Yellow
python -c "import torch; print('PyTorch version:', torch.__version__); print('CUDA available:', torch.cuda.is_available())"

Write-Host "`n=== Installation Complete ===" -ForegroundColor Green
Write-Host "You can now use 'dev --help' to test the CLI" -ForegroundColor Cyan
Write-Host "For other projects, run: pip install -e $cortexPath" -ForegroundColor Yellow