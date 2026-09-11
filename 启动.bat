@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "QuilliteImgBedBridge.exe" (
  echo [错误] 找不到 QuilliteImgBedBridge.exe
  echo        请确认本脚本与本程序位于同一目录。
  echo.
  echo 按任意键关闭本窗口...
  pause >nul
  exit /b 1
)

echo ================================================================
echo  Quillite-ImgBed Bridge  本地转接服务
echo  本窗口保持打开才会持续提供服务；关闭窗口即停止服务。
echo ================================================================
echo.

QuilliteImgBedBridge.exe -config "%~dp0config.json"

echo.
echo 服务已停止，按任意键关闭本窗口...
pause >nul
