Attribute VB_Name = "mR2"
Option Explicit
Public Type TA
    a As Long
    b As String
    c As String * 10
    d As Integer
    e As Double
    f As Boolean
    g As Byte
    h As Variant
    i As Object
    j As Single
    k As Currency
    l As Date
End Type
Public Sub Take(r As TA)
    Print #1, r.a; r.b
End Sub
Sub Main()
    Dim x As TA, y As TA
    Open App.Path & "\out.txt" For Output As #1
    x.a = 1: x.b = "bee": x.c = "cee": x.d = 2: x.e = 3
    y = x
    Take y
    Print #1, y.c; y.d; y.e
    Close #1
End Sub
