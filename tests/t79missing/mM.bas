Attribute VB_Name = "mM"
Option Explicit

' An omitted Optional Variant is a variant of VT_ERROR holding DISP_E_PARAMNOTFOUND.  With several
' calls in one procedure the compiler may fill that slot once, in a block before the calls, and
' pass it by reference; the decompiler then sees a local assigned "nothing"
' (seen in a real program; arrbase.missing_values writes MissingArg() there).
Private Sub Nayta(ByVal n As Long, Optional a As Variant, Optional b As Variant)
    Print #1, n, IsMissing(a), IsMissing(b), VarType(a), VarType(b)
    If Not IsMissing(a) Then Print #1, "a", a
    If Not IsMissing(b) Then Print #1, "b", b
End Sub

Private Function Ehka(Optional v As Variant) As Variant
    Ehka = v
End Function

Private Sub Muuta(Optional v As Variant)
    If IsMissing(v) Then
        Print #1, "puuttuu"
    Else
        Print #1, "on", v
    End If
End Sub

Private Sub Monta(ByVal k As Long)
    Dim i As Long
    Nayta 1
    Nayta 2, k
    Nayta 3, , k + 1
    For i = 1 To 3
        If i = k Then
            Nayta 10 + i
        ElseIf i > k Then
            Nayta 20 + i, , i
        Else
            Nayta 30 + i, i
        End If
        Muuta
    Next i
End Sub

Private Sub Kopio()
    Dim x As Variant
    x = Ehka()
    Print #1, IsMissing(x), VarType(x)
    Nayta 40, x
    Muuta x
    x = 7
    Muuta x
    x = Ehka(9)
    Muuta x
End Sub

Sub Main()
    Open "out.txt" For Output As #1
    Monta 2
    Monta 1
    Kopio
    Close #1
End Sub
