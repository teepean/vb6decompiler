Attribute VB_Name = "mT"
Option Explicit

Public Function nyt()
    On Error GoTo virhe
    nyt = Format(DateSerial(2018, 5, 6), "dd.mm.yyyy")
    Exit Function
virhe:
    Resume Next
End Function

Public Function polku(a As String, b As String)
    Dim s As String
    On Error GoTo virhe
    s = LCase("C:\TEMP")
    If a > "" Then s = s & "\" & a
    If b > "" Then s = s & "\" & b
    polku = s & "\"
    Exit Function
virhe:
    Resume Next
End Function
