Option Explicit
Dim shell, fso, scriptDir, scriptBase, batPath
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptBase = fso.GetBaseName(WScript.ScriptFullName)
batPath = fso.BuildPath(scriptDir, scriptBase & ".bat")
shell.Run Chr(34) & batPath & Chr(34), 0, False
