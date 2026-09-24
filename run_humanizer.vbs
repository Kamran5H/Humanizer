' Humanizer Pro — Resilient Background Launcher
Option Explicit
Dim WshShell, fso, q, appDir, py, logPath, i, candidates, cand
q = Chr(34)
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(appDir & "\humanizer_pro.py") Then
    candidates = Array( _
        "C:\Users\chkam\OneDrive\Desktop\BrandFinder\Humanizer", _
        "C:\Users\chkam\OneDrive\Desktop\Humanizer", _
        "C:\Users\chkam\Desktop\BrandFinder\Humanizer" _
    )
    For Each cand In candidates
        If fso.FileExists(cand & "\humanizer_pro.py") Then
            appDir = cand
            Exit For
        End If
    Next
End If

logPath = appDir & "\launch.log"
WshShell.CurrentDirectory = appDir

Function ServerUp()
  Dim h
  ServerUp = False
  On Error Resume Next
  Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  h.setTimeouts 1500, 1500, 1500, 1500
  h.Open "GET", "http://127.0.0.1:8000/", False
  h.Send
  If Err.Number = 0 And (h.Status = 200 Or h.Status = 302) Then ServerUp = True
  On Error GoTo 0
End Function

If ServerUp() Then
  WshShell.Run "http://127.0.0.1:8000/", 1, False
  WScript.Quit
End If

py = "C:\Users\chkam\AppData\Local\Programs\Python\Python314\python.exe"
If Not fso.FileExists(py) Then py = "python.exe"

WshShell.Run "cmd /c " & q & q & py & q & " humanizer_pro.py > " & q & logPath & q & " 2>&1" & q, 0, False

For i = 1 To 50        ' up to 25s
  WScript.Sleep 500
  If ServerUp() Then Exit For
Next

If ServerUp() Then
  WshShell.Run "http://127.0.0.1:8000/", 1, False
Else
  MsgBox "Humanizer Pro server did not respond on port 8000." & vbCrLf & "Check log: " & logPath, vbExclamation, "Humanizer Pro"
End If
