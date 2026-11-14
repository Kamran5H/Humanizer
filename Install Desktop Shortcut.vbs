' Humanizer Pro - creates or repairs the desktop shortcut.
'
' Double-click this file once (and again whenever you move the Humanizer
' folder). It makes a "Humanizer Pro" icon on your desktop that starts the
' studio silently in the background - no black console window - and opens it
' in your browser. Older Humanizer shortcuts on the desktop are re-pointed
' to this folder as well.
'
' Run with /quiet to skip the confirmation message.
Option Explicit

Dim shell, fso, appDir, launcher, icon, desktop, quiet, arg, fixed, name, f, lnk

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

quiet = False
For Each arg In WScript.Arguments
  If LCase(arg) = "/quiet" Then quiet = True
Next

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = appDir & "\run_humanizer.vbs"
icon = appDir & "\humanizer.ico"

If Not fso.FileExists(launcher) Then
  If Not quiet Then MsgBox "run_humanizer.vbs was not found in:" & vbCrLf & appDir, vbExclamation, "Humanizer Pro"
  WScript.Quit 1
End If

Sub PointAt(path)
  Dim s
  Set s = shell.CreateShortcut(path)
  s.TargetPath = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\wscript.exe"
  s.Arguments = Chr(34) & launcher & Chr(34)
  s.WorkingDirectory = appDir
  s.WindowStyle = 1
  s.Description = "Humanizer Pro - AI text naturalization & writing studio"
  If fso.FileExists(icon) Then s.IconLocation = icon & ",0"
  s.Save
End Sub

desktop = shell.SpecialFolders("Desktop")
fixed = 0

' Re-point any older Humanizer shortcut that is already on the desktop.
If fso.FolderExists(desktop) Then
  For Each f In fso.GetFolder(desktop).Files
    If LCase(fso.GetExtensionName(f.Name)) = "lnk" Then
      name = LCase(fso.GetBaseName(f.Name))
      If name <> "humanizer pro" And (InStr(name, "humanizer") > 0) Then
        Set lnk = shell.CreateShortcut(f.Path)
        If InStr(LCase(lnk.Arguments & lnk.TargetPath), "humanizer") > 0 Then
          PointAt f.Path
          fixed = fixed + 1
        End If
      End If
    End If
  Next
End If

PointAt desktop & "\Humanizer Pro.lnk"

If Not quiet Then
  MsgBox "The Humanizer Pro shortcut on your desktop is ready." & vbCrLf & vbCrLf & _
         "It starts the studio silently in the background and opens your browser." & vbCrLf & vbCrLf & _
         "Older shortcuts repaired: " & fixed, vbInformation, "Humanizer Pro"
End If
