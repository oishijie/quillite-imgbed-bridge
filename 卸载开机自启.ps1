# 取消 Quillite-ImgBed Bridge 的开机自启。

$ErrorActionPreference = 'Stop'

$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'Quillite-ImgBed Bridge.lnk'

if (Test-Path $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
    Write-Host "[完成] 已取消开机自启：$shortcutPath" -ForegroundColor Green
} else {
    Write-Host "[提示] 未找到自启快捷方式，可能本来就没有安装。" -ForegroundColor Yellow
}
