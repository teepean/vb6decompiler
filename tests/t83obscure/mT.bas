Attribute VB_Name = "mT"
Option Explicit
Option Compare Text
Option Base 1

' Option Compare Text changes every string compare in the module, Option Base the arrays' lower bound.
Public Function Sama(a As String, b As String) As Boolean
    Sama = (a = b)
End Function

Public Function Suurempi(a As String, b As String) As Boolean
    Suurempi = a > b
End Function

Public Function Mallina(a As String) As Boolean
    Mallina = a Like "AB*"
End Function

Public Function Paikka(a As String) As Long
    Paikka = InStr(a, "CD")
End Function

Public Function Korvaa(a As String) As String
    Korvaa = Replace(a, "x", "Y", , , vbTextCompare)
End Function

Public Function Valinta(a As String) As Long
    Select Case a
    Case "yksi"
        Valinta = 1
    Case "kaksi", "kolme"
        Valinta = 2
    Case Else
        Valinta = 3
    End Select
End Function

Public Function Pohja() As Long
    Dim x(3) As Integer
    x(1) = 7: x(3) = 9
    Pohja = LBound(x) * 100 + UBound(x) * 10 + x(1)
End Function
