Attribute VB_Name = "mM"
Option Explicit

' rtsuite runs a test's builds side by side: an app name of their own,
' from the build's folder (never printed)
Function AppName() As String
    Dim i As Long, h As Long, p As String
    p = App.Path
    For i = 1 To Len(p)
        h = (h * 31 + Asc(Mid$(p, i, 1))) And &HFFFFF
    Next i
    AppName = "VB6DecT27_" & h
End Function

Sub Main()
    Dim ap As String, s As String, v As Variant, i As Long, n As Long
    Dim sec As String, key As String, deflt As Variant
    Open App.Path & "\out.txt" For Output As #1
    ap = AppName()
    On Error Resume Next
    DeleteSetting ap
    On Error GoTo 0
    Print #1, "[" & GetSetting(ap, "Sec", "K1") & "]"
    Print #1, GetSetting(ap, "Sec", "K1", "fallback")
    SaveSetting ap, "Sec", "K1", "value1"
    SaveSetting ap, "Sec", "K2", 42
    sec = "Sec2": key = "Kx"
    SaveSetting ap, sec, key, "x" & 1 + 1
    Print #1, GetSetting(ap, "Sec", "K1")
    Print #1, GetSetting(ap, "Sec", "K2", "0") + 1
    s = GetSetting(ap, sec, key, "none")
    Print #1, s
    deflt = 99
    Print #1, GetSetting(ap, sec, "Missing", deflt)
    n = CLng(GetSetting(ap, "Sec", "K2"))
    Print #1, n * 2
    v = GetAllSettings(ap, "Sec")
    Print #1, IsArray(v); UBound(v, 1); UBound(v, 2)
    For i = LBound(v, 1) To UBound(v, 1)
        Print #1, v(i, 0); "="; v(i, 1)
    Next i
    v = GetAllSettings(ap, "NoSuchSection")
    Print #1, IsEmpty(v)
    DeleteSetting ap, "Sec", "K1"
    Print #1, GetSetting(ap, "Sec", "K1", "gone")
    Print #1, GetSetting(ap, "Sec", "K2", "gone")
    DeleteSetting ap, "Sec"
    Print #1, GetSetting(ap, "Sec", "K2", "gone2")
    Print #1, GetSetting(ap, sec, key, "gone3")
    On Error Resume Next
    DeleteSetting ap, "NoSuchSection"
    Print #1, Err.Number
    Err.Clear
    DeleteSetting ap
    Print #1, Err.Number
    On Error GoTo 0
    Print #1, GetSetting(ap, sec, key, "gone4")
    Close #1
End Sub
