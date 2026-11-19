Attribute VB_Name = "mM"
Option Explicit

Private Declare Function lstrlenW Lib "kernel32" (ByVal p As Long) As Long

Function Peek(ByVal p As Long, ByVal k As Long) As Long
    Peek = k + 1
End Function

Sub Main()
    Dim x As Long, y As Long
    Open App.Path & "\out.txt" For Output As #1
    x = 10
    Print #1, Peek(VarPtr(x), 7); x
    y = 20
    Print #1, lstrlenW(VarPtr(y)); y
    Close #1
End Sub
