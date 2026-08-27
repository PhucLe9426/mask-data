[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectDirectory = $PSScriptRoot

try {
    Set-Location -LiteralPath $projectDirectory

    # start.ps1 tự mở Docker Desktop, build image và chờ app/database healthy.
    & (Join-Path $projectDirectory "start.ps1") -Rebuild -NoBrowser
    if ($LASTEXITCODE -ne 0) {
        throw "Không thể chuẩn bị môi trường Docker để chạy test."
    }

    Write-Host "`nDang chay automated tests..." -ForegroundColor Cyan
    docker compose exec -T app python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Có automated test thất bại."
    }

    Write-Host "`nTat ca automated tests da thanh cong." -ForegroundColor Green
}
catch {
    Write-Host "`nKhong the chay test: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
