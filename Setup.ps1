param([string]$Python='python')
$ErrorActionPreference='Stop'
$studioRoot=$PSScriptRoot
if(-not (Test-Path -LiteralPath "$studioRoot\.venv\Scripts\python.exe")) {
    & $Python -m venv "$studioRoot\.venv"
    if($LASTEXITCODE -ne 0){throw 'Install Python 3.11 or 3.12, then run Setup.ps1 again.'}
}
& "$studioRoot\.venv\Scripts\python.exe" -m pip install -r "$studioRoot\requirements.lock.txt"
if($LASTEXITCODE -ne 0){throw 'Dependency installation failed.'}
Write-Output 'Setup complete. Run Launch.ps1. AI rendering also requires your configured ComfyUI installation and model weights; see README.md.'
