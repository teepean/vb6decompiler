VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cK"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
' A member's fixed 2 dimensional array: x + y * 10 with one bounds check per subscript.
' (a real program's two-dimensional array lost its second dimension: subscript errors.)
Private t(0 To 9, 0 To 4) As Long

Public Sub Taulu(n As Integer)
    Dim x As Integer, y As Integer
    For x = 0 To 9
        For y = n - 2 To n + 2
            If y >= 0 And y < 5 Then t(x, y) = x * 10 + y
        Next y
    Next x
    Print #1, "taulu", t(3, 2), t(9, 4), t(0, 1)
End Sub

' A ByRef last parameter set and read, recursively: no result pointer, and a local copied into
' it is no result slot (a real program's class procedure came out a Function: its array offsets took the name).
Private Sub Syvennä(k As Long, n As Integer, r As Long)
    Dim a As Long, b As Long, m As Long
    If n < 3 Then
        Syvennä k * 2, n + 1, r
        a = r
        Syvennä k * 2 + 1, n + 1, r
        b = r
    Else
        a = k: b = k + 1
    End If
    m = (a + b) \ 2
    t(n, 1) = m
    r = m
End Sub

Public Sub Puu()
    Dim r As Long
    Syvennä 1, 1, r
    Print #1, "puu", r, t(1, 1), t(2, 1), t(3, 1)
End Sub

Public Sub Kaksi(x As Variant, y As Variant)
    Print #1, "Kaksi", x, y
End Sub

Public Property Let Paikka(v As Variant)
    Print #1, "paikka", v
End Property
