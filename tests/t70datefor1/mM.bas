Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim dt As Date, a As Date, b As Date, n As Long
    Open App.Path & "\out.txt" For Output As #1
    a = DateSerial(2020, 2, 27): b = DateSerial(2020, 3, 1)
    For dt = a To b
        Print #1, Format$(dt, "mm-dd")
        ' (guard: the decompiled loop must not run for ever)
        n = n + 1
        If n > 8 Then Exit For
    Next dt
    Close #1
End Sub
