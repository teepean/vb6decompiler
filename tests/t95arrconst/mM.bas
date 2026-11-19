Attribute VB_Name = "mM"
Option Explicit

' Constants stored in arrays of 8-byte numbers take two integer moves.  What the arrays are comes from
' how they are read, or from what they are given to.
Dim gd(3) As Double
Dim gc(3) As Currency

Sub Locals()
    Dim a(3) As Double, c(3) As Currency, s(3) As Single, i As Integer
    a(0) = 1.5: a(1) = -1000: a(2) = 0.1
    c(0) = 1.5@: c(1) = 100@
    s(0) = 2.5: s(1) = 0.1
    For i = 0 To 2
        Print #1, a(i); c(i); s(i)
    Next i
End Sub

Sub Modules()
    Dim i As Integer
    gd(0) = 1.5: gd(1) = -1000: gd(2) = 0.1
    gc(0) = 1.5@: gc(1) = 100@
    For i = 0 To 2
        Print #1, gd(i); gc(i)
    Next i
End Sub

Sub Dynamic()
    Dim a() As Double, i As Integer
    ReDim a(2)
    a(0) = 1.5: a(1) = -1000: a(2) = 0.1
    For i = 0 To 2
        Print #1, a(i)
    Next i
End Sub

Sub OnlyGiven()
    Dim a(3) As Double
    a(0) = -1000: a(1) = 400: a(2) = 400: a(3) = 400
    Print #1, Round(NPV(0.1, a), 4); Round(IRR(a), 4); Round(MIRR(a, 0.1, 0.1), 4)
End Sub

Sub TakesArray(a() As Double)
    a(0) = a(0) + 1
End Sub

Sub OnlyPassed()
    Dim a(3) As Double
    a(0) = 2.5
    TakesArray a
    Print #1, a(0)
End Sub

Sub Main()
    Open "out.txt" For Output As #1
    Locals
    Modules
    Dynamic
    OnlyGiven
    OnlyPassed
    Close #1
End Sub
