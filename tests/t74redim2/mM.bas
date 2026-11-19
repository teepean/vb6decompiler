Attribute VB_Name = "mM"
Option Explicit
Type TR
    x As Integer
    y As Long
End Type
Dim g() As TR
Dim h() As Long

Sub Main()
    Dim l() As String, i As Long, j As Long, n As Long, s As String
    Open App.Path & "\out.txt" For Output As #1
    n = 3
    ReDim g(2, 1)
    ReDim h(1 To n, 0 To 4)
    ReDim l(n, n + 1)
    For i = 0 To 2
        For j = 0 To 1
            g(i, j).x = i * 10 + j
            g(i, j).y = i * 100 + j
        Next j
    Next i
    For i = 1 To n
        For j = 0 To 4
            h(i, j) = i * 7 + j
        Next j
    Next i
    l(3, 4) = "end"
    l(0, 0) = "start"
    For i = 0 To 2
        Print #1, g(i, 1).x; g(i, 0).y
    Next i
    Print #1, h(3, 4); h(1, 0); UBound(h, 1); UBound(h, 2); LBound(h, 1)
    Print #1, l(3, 4); l(0, 0); UBound(l, 2)
    ReDim Preserve h(1 To n, 0 To 6)
    h(2, 6) = 99
    Print #1, h(2, 6); h(2, 4); UBound(h, 2)
    Close #1
End Sub
