# -*- coding: utf-8 -*-
"""把启动脚本重写为 Windows 原生格式：GBK(936) 编码 + CRLF 换行。

背景：cmd.exe 解析 .bat 时使用系统 ANSI 代码页（中文 Windows 为 GBK/936），
WScript 解析 .vbs 同理。若文件是 UTF-8 无 BOM + LF 换行，会出现
中文乱码 + 按行切分错乱（如把 "echo" 切碎），导致双击闪退。

用法：python scripts/fix-scripts-encoding.py
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BAT = '''@echo off
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
'''

VBS = '''
' 后台静默启动 Quillite-ImgBed Bridge（无控制台窗口）。
' 日志写入 config.json 中 logFile 指定的文件（默认 bridge.log）。
Dim shell, fso, scriptDir

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

If Not fso.FileExists(scriptDir & "\\QuilliteImgBedBridge.exe") Then
  MsgBox "找不到 QuilliteImgBedBridge.exe，请确认与本脚本位于同一目录。", 16, "Quillite-ImgBed Bridge"
  WScript.Quit 1
End If

shell.Run """" & scriptDir & "\\QuilliteImgBedBridge.exe"" -config """ & scriptDir & "\\config.json""", 0, False
'''

TARGETS = [
    # bat 走 UTF-8 + chcp 65001：与 Go 程序输出的 UTF-8 统一，中文都正常
    ("启动.bat", BAT, "utf-8"),
    # vbs 由 WScript 按系统 ANSI 解析，中文必须用 GBK
    ("静默启动.vbs", VBS, "gbk"),
]


def main():
    failed = False
    for name, text, encoding in TARGETS:
        path = os.path.join(BASE, name)
        try:
            with open(path, "w", encoding=encoding, newline="\r\n") as handle:
                handle.write(text)
        except Exception as exc:
            print("[FAIL] %s 写入失败: %s" % (name, exc))
            failed = True
            continue

        raw = open(path, "rb").read()
        has_bom = raw[:3] == b"\xef\xbb\xbf"
        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n")
        style = "CRLF" if lf and crlf == lf else ("LF-only" if crlf == 0 else "混合")
        bare_lf = lf - crlf
        try:
            raw.decode(encoding)
            decode_ok = True
        except Exception:
            decode_ok = False
        print(
            "[PASS] %-16s 编码=%-6s 字节=%-5d 换行=%-8s 裸LF=%-3d BOM=%-5s 可解码=%s"
            % (name, encoding, len(raw), style, bare_lf, has_bom, decode_ok)
        )
        if style != "CRLF" or has_bom or not decode_ok:
            failed = True

    print("结果：%s" % ("全部通过" if not failed else "存在问题"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
