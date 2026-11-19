Attribute VB_Name = "mM"
Option Explicit
Option Compare Text

Sub Main()
    Dim s As String, t As String, a As Variant, b As Variant, f As Boolean
    Open App.Path & "\out.txt" For Output As #1
    s = "abc": t = "ABC": a = "Abc": b = "ABD"
    Print #1, s = t; s <> t; s < t; s > "ABB"; s <= t; s >= "ABD"
    Print #1, a = s; a <> s; a < b; a > b; a <= b; a >= b; a = b
    If a = "aBC" Then Print #1, "if eq"
    If a <> "aBC" Then Print #1, "if ne" Else Print #1, "else ne"
    If a < "ABD" Then Print #1, "if lt"
    If a > "ABD" Then Print #1, "if gt" Else Print #1, "else gt"
    If a <= "ABC" Then Print #1, "if le"
    If a >= "ABC" Then Print #1, "if ge"
    Print #1, s Like "A*"; s Like t; a Like "a?C"; a Like b; s Like "[A-B]BC"
    f = t Like a
    Print #1, f
    Print #1, StrComp(s, t); InStr(s, "B"); InStr(1, s, "B"); InStrRev(s, "C"); Replace(s, "B", "x")
    Select Case s
        Case "ABC": Print #1, "case abc"
        Case Else: Print #1, "case else"
    End Select
    Select Case a
        Case "ABC": Print #1, "vcase abc"
        Case Else: Print #1, "vcase else"
    End Select
    Print #1, Join(Filter(Array("ab", "AB", "c"), "aB"), ",")
    Close #1
End Sub
