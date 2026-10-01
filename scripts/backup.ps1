# Windows 备份：MySQL dump + media + chroma
# 用法: .\scripts\backup.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutDir = if ($env:BACKUP_DIR) { Join-Path $env:BACKUP_DIR $Stamp } else { Join-Path $Root "backups\$Stamp" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$DbHost = if ($env:DB_HOST) { $env:DB_HOST } else { "127.0.0.1" }
$DbPort = if ($env:DB_PORT) { $env:DB_PORT } else { "3306" }
$DbName = if ($env:DB_NAME) { $env:DB_NAME } else { "smartkbqa" }
$DbUser = if ($env:DB_USER) { $env:DB_USER } else { "root" }
$DbPassword = if ($env:DB_PASSWORD) { $env:DB_PASSWORD } else { "" }

Write-Host "[backup] writing to $OutDir"

$mysqldump = Get-Command mysqldump -ErrorAction SilentlyContinue
if ($mysqldump) {
    $env:MYSQL_PWD = $DbPassword
    $sqlPath = Join-Path $OutDir "mysql_${DbName}.sql"
    & mysqldump -h $DbHost -P $DbPort -u $DbUser --single-transaction --routines --triggers $DbName | Out-File -Encoding utf8 $sqlPath
    Compress-Archive -Path $sqlPath -DestinationPath "$sqlPath.zip" -Force
    Remove-Item $sqlPath -Force
    Write-Host "[backup] mysql dump ok"
} else {
    Write-Host "[backup] skip mysql: mysqldump not found" -ForegroundColor Yellow
}

$media = Join-Path $Root "media"
if (Test-Path $media) {
    Compress-Archive -Path $media -DestinationPath (Join-Path $OutDir "media.zip") -Force
    Write-Host "[backup] media ok"
}

$chroma = Join-Path $Root "data\chroma"
if (Test-Path $chroma) {
    Compress-Archive -Path $chroma -DestinationPath (Join-Path $OutDir "chroma.zip") -Force
    Write-Host "[backup] chroma ok"
}

$backupRoot = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $Root "backups" }
if (Test-Path $backupRoot) {
    Get-ChildItem $backupRoot -Directory |
        Sort-Object Name -Descending |
        Select-Object -Skip 14 |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
}

Write-Host "[backup] done: $OutDir"
Get-ChildItem $OutDir | Format-Table Name, Length
