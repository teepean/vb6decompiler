Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim i As Long, n As Long, v As Variant
    Open App.Path & "\out.txt" For Output As #1
    For i = 0 To 15
        Print #1, QBColor(i);
    Next i
    Print #1,
    n = QBColor(9) And &HFF
    Print #1, n
    v = 4
    Print #1, QBColor(v) + 1
    Beep
    Print #1, "beep passed"
    Close #1
End Sub
