param([switch]$OpenBrowser)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
New-Item -ItemType Directory -Path "$root\logs" -Force | Out-Null
$python=Join-Path $root '.venv\Scripts\python.exe'
if(-not (Test-Path -LiteralPath $python)){$python=Join-Path $env:USERPROFILE 'ComfyUI-Installs\H3-Image\.venv\Scripts\python.exe'}
if(-not (Test-Path -LiteralPath $python)){throw 'Run Setup.ps1 first.'}
$ready=$false
try { $h=Invoke-RestMethod 'http://127.0.0.1:8790/api/health' -TimeoutSec 2; $ready=$h.app -eq 'AI Image Studio' } catch {}
if (-not $ready) {
    if (Get-NetTCPConnection -State Listen -LocalPort 8790 -ErrorAction SilentlyContinue) { throw 'Port 8790 is occupied by another application.' }
    $env:OMP_NUM_THREADS='2'; $env:MKL_NUM_THREADS='2'; $env:PYTHONUTF8='1'
    $p=Start-Process -FilePath $python -ArgumentList '-m uvicorn backend.app:app --host 127.0.0.1 --port 8790 --no-access-log' -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput "$root\logs\app.stdout.log" -RedirectStandardError "$root\logs\app.stderr.log"
    $p.PriorityClass='BelowNormal'
    for($i=0;$i -lt 30;$i++){
        Start-Sleep -Milliseconds 500
        try { $h=Invoke-RestMethod 'http://127.0.0.1:8790/api/health' -TimeoutSec 1; if($h.app -eq 'AI Image Studio'){$ready=$true;break} } catch {}
    }
}
if(-not $ready){throw 'Studio did not start. See logs\app.stderr.log.'}
if($OpenBrowser){Start-Process 'http://127.0.0.1:8790'}
Write-Output 'AI Image Studio: http://127.0.0.1:8790'
