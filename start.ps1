[CmdletBinding()]
param(
    [switch]$Rebuild,
    [switch]$NoBrowser,
    [switch]$OpenPgAdmin
)

$ErrorActionPreference = "Stop"
$projectDirectory = $PSScriptRoot
$webUrl = "http://127.0.0.1:8080/"
$pgAdminUrl = "http://127.0.0.1:5051/"
$healthUrl = "http://127.0.0.1:8080/health"
$dockerDesktopPath = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
$dockerCliPath = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"

function Get-DockerCommand {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($dockerCommand) {
        return $dockerCommand.Source
    }

    if (Test-Path -LiteralPath $dockerCliPath) {
        return $dockerCliPath
    }

    throw "Khong tim thay Docker. Hay cai Docker Desktop truoc khi chay project."
}

function Test-DockerEngine([string]$DockerCommand) {
    # Docker CLI ghi lỗi ra stderr khi Desktop chưa chạy. Đây là trạng thái
    # bình thường để script tiếp tục mở Docker, không phải lỗi cần dừng script.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $DockerCommand info *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

try {
    Set-Location -LiteralPath $projectDirectory
    $docker = Get-DockerCommand

    if (-not (Test-DockerEngine $docker)) {
        if (-not (Test-Path -LiteralPath $dockerDesktopPath)) {
            throw "Docker chua hoat dong va khong tim thay Docker Desktop."
        }

        Write-Host "Dang khoi dong Docker Desktop..." -ForegroundColor Cyan
        Start-Process -FilePath $dockerDesktopPath

        $dockerReady = $false
        for ($attempt = 1; $attempt -le 90; $attempt++) {
            Start-Sleep -Seconds 2
            if (Test-DockerEngine $docker) {
                $dockerReady = $true
                break
            }
        }

        if (-not $dockerReady) {
            throw "Docker Desktop khong san sang sau 3 phut. Hay mo Docker Desktop va thu lai."
        }
    }

    Write-Host "Dang khoi dong FastAPI, PostgreSQL va pgAdmin..." -ForegroundColor Cyan
    $composeArguments = @("compose", "up", "-d")
    if ($Rebuild) {
        $composeArguments += "--build"
    }

    & $docker @composeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose khoi dong project that bai."
    }

    Write-Host "Dang cho API san sang..." -ForegroundColor Cyan
    $apiReady = $false
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
            if ($response.status -eq "ok" -and $response.database -eq "ok") {
                $apiReady = $true
                break
            }
        }
        catch {
            # API van dang khoi dong.
        }
        Start-Sleep -Seconds 2
    }

    if (-not $apiReady) {
        & $docker compose ps
        throw "API chua san sang. Chay 'docker compose logs app' de xem loi."
    }

    Write-Host "Project da san sang: $webUrl" -ForegroundColor Green
    Write-Host "pgAdmin: $pgAdminUrl" -ForegroundColor Green
    Write-Host "Dang nhap pgAdmin: admin@masking-app.com / pgadmin_dev_password" -ForegroundColor DarkGray
    if (-not $NoBrowser) {
        Start-Process $webUrl
    }
    if ($OpenPgAdmin) {
        Start-Process $pgAdminUrl
    }
}
catch {
    Write-Host "`nKhong the khoi dong project: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Nhan Enter de dong cua so..."
    [void](Read-Host)
    exit 1
}
