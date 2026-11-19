Attribute VB_Name = "mM"
Option Explicit

' Functions returning small records: up to 8 bytes they come back in edx:eax, not through a pointer.
Public Type TVec
    x As Single
    y As Single
End Type
Public Type TPari
    a As Long
    b As Long
End Type
Public Type TKolme
    a As Long
    b As Integer
    c As Integer
End Type
Public Type TLyhyt
    x As Integer
    y As Integer
End Type
Public Type TIso
    a As Long
    b As Long
    c As Long
End Type
Public Type TOlio
    nimi As String
    paikka As TVec
    nopeus As TVec
    energia As Single
    jalki(1 To 3) As TVec
End Type
Public olio() As TOlio
Public yksi As TVec

Public Function VSet(ByVal a As Single, ByVal b As Single) As TVec
    VSet.x = a
    VSet.y = b
End Function

Public Function VAdd(v1 As TVec, v2 As TVec) As TVec
    VAdd.x = v1.x + v2.x
    VAdd.y = v1.y + v2.y
End Function

Public Function VSub(v1 As TVec, v2 As TVec) As TVec
    VSub.x = v1.x - v2.x
    VSub.y = v1.y - v2.y
End Function

Public Function VScale(v As TVec, k As Single) As TVec
    If Abs(k) > 100 Then k = Sgn(k) * 100
    VScale.x = v.x * k
    VScale.y = v.y * k
End Function

Public Function VDot(v1 As TVec, v2 As TVec) As Single
    VDot = v1.x * v2.x + v1.y * v2.y
End Function

Public Function VPass(v As TVec) As TVec
    VPass = v
End Function

Public Function VThru(v As TVec) As TVec
    VThru = VAdd(v, VSet(1, 1))
End Function

Public Function Pari(ByVal a As Long, ByVal b As Long) As TPari
    Pari.a = a
    Pari.b = b
End Function

Public Function Kolme(ByVal a As Long, ByVal b As Integer, ByVal c As Integer) As TKolme
    Kolme.a = a
    Kolme.b = b
    Kolme.c = c
End Function

Public Function Lyhyt(ByVal x As Integer, ByVal y As Integer) As TLyhyt
    Lyhyt.x = x
    Lyhyt.y = y
End Function

Public Function Iso(ByVal a As Long) As TIso
    Iso.a = a
    Iso.b = a + 1
    Iso.c = a + 2
End Function

Public Function Raha(ByVal a As Long) As Currency
    Raha = a * 1.5@
End Function

Public Function Raha2(c As Currency) As Currency
    Raha2 = c
End Function

Public Function Tupla(ByVal a As Double) As Double
    Tupla = a * 2
End Function

Private Sub NV(ByVal n As String, v As TVec)
    Print #1, n, v.x, v.y
End Sub

Sub Main()
    Dim a As TVec, b As TVec, c As TVec
    Dim p As TPari, k As TKolme, l As TLyhyt, i As TIso
    Dim r As Currency
    Dim n As Long
    Dim e As Single
    Open "out.txt" For Output As #1
    a = VSet(1.5, 2.5)
    b = VSet(3, 4)
    NV "a", a
    c = VAdd(a, b)
    NV "add", c
    c = VSub(VAdd(a, b), VScale(a, 2))
    NV "nest", c
    NV "arg", VAdd(c, c)
    e = VDot(VAdd(a, b), b)
    Print #1, e
    e = 200
    c = VScale(a, e)
    NV "scale", c
    Print #1, e
    c = VPass(a)
    NV "pass", c
    c = VThru(b)
    NV "thru", c
    yksi = VSet(9, 8)
    NV "glob", yksi
    p = Pari(11, 12)
    Print #1, p.a, p.b
    k = Kolme(21, 22, 23)
    Print #1, k.a, k.b, k.c
    l = Lyhyt(31, 32)
    Print #1, l.x, l.y
    i = Iso(41)
    Print #1, i.a, i.b, i.c
    r = Raha(10)
    Print #1, r, Raha2(r)
    Print #1, Tupla(2.5)
    ReDim olio(1 To 3)
    For n = 1 To 3
        olio(n).nimi = "o" & n
        olio(n).paikka = VSet(n, n * 2)
        olio(n).nopeus = VSet(0.5, 0.25)
        olio(n).jalki(2) = VSet(n, 0)
    Next n
    For n = 1 To 3
        With olio(n)
            .paikka = VAdd(.paikka, .nopeus)
            .energia = VDot(.paikka, .nopeus) + .jalki(2).x
            .jalki(1) = .paikka
            NV .nimi, .paikka
            NV .nimi, .jalki(1)
            Print #1, .energia
        End With
    Next n
    a = VSub(olio(1).paikka, olio(2).paikka)
    NV "kahden", a
    Print #1, VDot(olio(3).paikka, VSet(1, 0))
    Close #1
End Sub
