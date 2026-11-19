Attribute VB_Name = "mM"
Option Explicit

Function Bump(p As Long) As Long
    p = p + 1
    Bump = p * 10
End Function

Sub Main()
    Dim x As Long, y As Long, z As Long
    Open App.Path & "\out.txt" For Output As #1
    x = 10
    y = Bump(x)
    Print #1, y; x
    z = 20
    Print #1, Bump(z); z
    Print #1, "next"
    Close #1
End Sub
