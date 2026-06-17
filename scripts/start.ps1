# EquityScope - start local development
# Usage from project root: .\scripts\start.ps1

param(
    [switch]$NoFrontend
)

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$VenvUvicorn = Join-Path $Root ".venv\Scripts\uvicorn.exe"

function Write-Step($msg) { Write-Host "`n[start] $msg" -ForegroundColor Cyan }
function Write-OK($msg) { Write-Host "  OK $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

Write-Step "Killing any stale processes on port 8000..."
$pids = (netstat -ano | Select-String ":8000 " | Select-String LISTENING |
         ForEach-Object { ($_ -split '\s+')[-1] }) | Sort-Object -Unique
foreach ($pid in $pids) {
    if ($pid -match '^\d+$') {
        Write-Warn "Killing PID $pid on port 8000"
        taskkill /PID $pid /F 2>$null | Out-Null
    }
}
Start-Sleep -Milliseconds 500
Write-OK "Port 8000 is free"

Write-Step "Starting FastAPI backend with local storage on port 8000..."
$backendCmd = "cd `"$Backend`"; `$env:RUNTIME_STORAGE='local'; `"$VenvUvicorn`" app.main:app --host 0.0.0.0 --port 8000 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd -WindowStyle Normal

Write-Host "  Waiting for backend..." -NoNewline
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    Write-Host "." -NoNewline
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/healthz" -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
}
Write-Host ""
if ($ready) {
    Write-OK "Backend is ready at http://localhost:8000"
} else {
    Write-Warn "Backend not ready after 30s; check the backend terminal window"
}

if (-not $NoFrontend) {
    Write-Step "Starting Angular frontend on port 4200..."
    $frontendCmd = "cd `"$Frontend`"; npx ng serve"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd -WindowStyle Normal
    Write-OK "Frontend starting at http://localhost:4200"
}

Write-Host ""
Write-Host "EquityScope is starting up" -ForegroundColor Cyan
Write-Host "  Backend:  http://localhost:8000" -ForegroundColor White
Write-Host "  Frontend: http://localhost:4200" -ForegroundColor White
Write-Host "  API docs: http://localhost:8000/docs" -ForegroundColor White
