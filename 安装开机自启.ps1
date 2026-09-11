# 把 Quillite-ImgBed Bridge 加入开机自启（在「启动」文件夹创建快捷方式）。
# 以管理员身份运行不是必需的，但可以从任意位置执行。

$ErrorActionPreference = 'Stop'

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs = Join-Path $dir '静默启动.vbs'
$exe = Join-Path $dir 'QuilliteImgBedBridge.exe'
$config = Join-Path $dir 'config.json'

foreach ($required in @($vbs, $exe)) {
    if (-not (Test-Path $required)) {
        Write-Host "[错误] 缺少文件：$required" -ForegroundColor Red
        exit 1
    }
}

if (-not (Test-Path $config)) {
    Write-Host "[提示] 还没有 config.json，首次启动时会自动生成，记得填写 baseUrl 和 authCode。" -ForegroundColor Yellow
}

$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'Quillite-ImgBed Bridge.lnk'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$shortcut.Arguments = '"' + $vbs + '"'
$shortcut.WorkingDirectory = $dir
$shortcut.Description = '轻阅 Markdown 与 CloudFlare-ImgBed 的本地转接服务（开机静默启动）'
$shortcut.Save()

Write-Host "[完成] 已加入开机自启：" -ForegroundColor Green
Write-Host "        $shortcutPath"
Write-Host "        指向 $vbs"
Write-Host ""
Write-Host "        取消自启请运行：卸载开机自启.ps1"
