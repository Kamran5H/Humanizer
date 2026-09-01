Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\chkam\OneDrive\Desktop\BrandFinder\Humanizer"
WshShell.Run "pythonw.exe humanizer_pro.py", 0, False
