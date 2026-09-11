
' 后台静默启动 Quillite-ImgBed Bridge（无控制台窗口）。
' 日志写入 config.json 中 logFile 指定的文件（默认 bridge.log）。
Dim shell, fso, scriptDir

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir

If Not fso.FileExists(scriptDir & "\QuilliteImgBedBridge.exe") Then
  MsgBox "找不到 QuilliteImgBedBridge.exe，请确认与本脚本位于同一目录。", 16, "Quillite-ImgBed Bridge"
  WScript.Quit 1
End If

shell.Run """" & scriptDir & "\QuilliteImgBedBridge.exe"" -config """ & scriptDir & "\config.json""", 0, False
