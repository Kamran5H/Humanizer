' Humanizer Pro — silent Windows launcher.
' Double-click to start the web studio in the background and open it in your browser.
' Automatically locates .venv, py.exe, or standard Windows Python installs.
Option Explicit
Dim WshShell, fso, q, appDir, py, logPath, i

q = Chr(34)
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
logPath = appDir & "\launch.log"
WshShell.CurrentDirectory = appDir

If Not fso.FileExists(WshShell.SpecialFolders("Desktop") & "\Humanizer Pro.lnk") _
   And fso.FileExists(appDir & "\Install Desktop Shortcut.vbs") Then
  WshShell.Run "wscript //nologo " & q & appDir & "\Install Desktop Shortcut.vbs" & q & " /quiet", 0, False
End If

Function ServerUp()
  Dim h
  ServerUp = False
  On Error Resume Next
  Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  h.setTimeouts 1500, 1500, 1500, 1500
  h.Open "GET", "http://127.0.0.1:8000/api/health", False
  h.Send
  If Err.Number = 0 And h.Status = 200 Then ServerUp = True
  On Error GoTo 0
End Function

If ServerUp() Then
  WshShell.Run "http://127.0.0.1:8000/", 1, False
  WScript.Quit
End If

Function FindPython()
  Dim localAppData, progDir, subFolder
  If fso.FileExists(appDir & "\.venv\Scripts\python.exe") Then
    FindPython = appDir & "\.venv\Scripts\python.exe"
    Exit Function
  End If
  localAppData = WshShell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
  progDir = localAppData & "\Programs\Python"
  If fso.FolderExists(progDir) Then
    For Each subFolder In fso.GetFolder(progDir).SubFolders
      If fso.FileExists(subFolder.Path & "\python.exe") Then
        FindPython = subFolder.Path & "\python.exe"
        Exit Function
      End If
    Next
  End If
  If fso.FileExists(WshShell.ExpandEnvironmentStrings("%SystemRoot%") & "\py.exe") Then
    FindPython = WshShell.ExpandEnvironmentStrings("%SystemRoot%") & "\py.exe"
    Exit Function
  End If
  FindPython = "python"
End Function

py = FindPython()

' If .venv does not exist yet, run Start Humanizer.bat once to install Python/venv
If Not fso.FileExists(appDir & "\.venv\Scripts\python.exe") Then
  WshShell.Run q & appDir & "\Start Humanizer.bat" & q, 1, False
  WScript.Quit
End If

WshShell.Run "cmd /c " & q & q & py & q & " humanizer_pro.py --web --no-browser > " & q & logPath & q & " 2>&1" & q, 0, False

For i = 1 To 60        ' wait up to 30 s
  WScript.Sleep 500
  If ServerUp() Then Exit For
Next

If ServerUp() Then
  WshShell.Run "http://127.0.0.1:8000/", 1, False
Else
  MsgBox "Humanizer Pro did not start on port 8000." & vbCrLf & "See the log: " & logPath, vbExclamation, "Humanizer Pro"
End If
