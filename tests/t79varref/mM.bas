Attribute VB_Name = "mM"
Option Explicit

' A ByRef Variant parameter given a default and then a String (Left$): the assignments alone
' look like a String's.  The callers' Variant locals say otherwise.
Private Function Lue(ByVal k As String, ByVal oletus As Variant, tulos As Variant) As Long
    Dim p As String
    tulos = oletus
    If k = "x" Then
        p = Space$(8)
        Mid$(p, 1, 3) = "abc"
        tulos = Left$(p, 3)
        Lue = 1
    ElseIf k = "n" Then
        tulos = Len(k) + 41
        Lue = 2
    End If
End Function

Private Sub Kutsu(ByVal k As String)
    Dim v As Variant
    Lue k, "ei", v
    Print #1, k, VarType(v), v
    If v <> "ei" Then
        Print #1, "muu"
    End If
End Sub

' A String array filled by a class method, through a local (a registry class's EnumerateSections)
Sub Main()
    Open "out.txt" For Output As #1
    Kutsu "x"
    Kutsu "n"
    Kutsu "z"
    Close #1
End Sub
