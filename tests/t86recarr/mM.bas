Attribute VB_Name = "mM"
Option Explicit

' A record with Strings has a descriptor listing what needs cleanup, and its fixed arrays carry a
' full SAFEARRAY there: every kind of element, lower bounds, two dimensions, and fields after them.
Public Type TSis
    nimi As String
    arvo As Integer
End Type
Public Type TVali
    a As Long
    b As Single
End Type
Public Type TIso
    otsikko As String
    kokonaiset(1 To 4) As Integer
    pitkat(0 To 3) As Long
    liu(2 To 5) As Single
    tupla(3) As Double
    tavut(1 To 6) As Byte
    tot(1 To 2) As Boolean
    rahat(1 To 2) As Currency
    paivat(1 To 4) As Long
    jonot(1 To 3) As String
    kiinteat(1 To 2) As String * 5
    sis(1 To 3) As TSis
    vali(1 To 2) As TVali
    ruudut(1 To 2, 0 To 2) As Long
    variantit(1 To 2) As Variant
    vinkki As Variant
    dyn() As Long
    dynrec() As TSis
    olio As Object
    loppu As String
    viimeinen As Long
End Type
Public kaikki(1 To 2) As TIso
Public yksi As TIso

Private Sub Tayta(r As TIso, ByVal k As Long)
    Dim i As Long, j As Long
    r.otsikko = "o" & k
    For i = 1 To 4: r.kokonaiset(i) = k + i: Next i
    For i = 0 To 3: r.pitkat(i) = 100 * k + i: Next i
    For i = 2 To 5: r.liu(i) = k + i / 4: Next i
    For i = 0 To 3: r.tupla(i) = k + i / 8: Next i
    For i = 1 To 6: r.tavut(i) = k + i: Next i
    r.tot(1) = True
    r.rahat(2) = 1.5 * k
    For i = 1 To 3
        r.jonot(i) = "j" & (k + i)
        r.sis(i).nimi = "s" & i
        r.sis(i).arvo = i * k
    Next i
    r.kiinteat(2) = "abc"
    r.vali(2).a = k
    r.vali(2).b = k / 2
    For i = 1 To 2
        For j = 0 To 2
            r.ruudut(i, j) = 10 * i + j
        Next j
    Next i
    r.variantit(2) = "v" & k
    r.vinkki = k * 2
    ReDim r.dyn(1 To 3)
    r.dyn(2) = k + 7
    ReDim r.dynrec(0 To 1)
    r.dynrec(1).nimi = "d" & k
    Set r.olio = New Collection
    r.olio.Add k
    r.loppu = "L" & k
    r.viimeinen = k + 1000
End Sub

Private Sub Nayta(r As TIso)
    Dim i As Long, j As Long
    Print #1, r.otsikko, r.loppu, r.viimeinen
    For i = 1 To 4: Print #1, r.kokonaiset(i);: Next i
    Print #1,
    For i = 0 To 3: Print #1, r.pitkat(i);: Next i
    Print #1,
    For i = 2 To 5: Print #1, r.liu(i);: Next i
    Print #1,
    For i = 0 To 3: Print #1, r.tupla(i);: Next i
    Print #1,
    For i = 1 To 6: Print #1, r.tavut(i);: Next i
    Print #1,
    Print #1, r.tot(1), r.tot(2), r.rahat(2)
    For i = 1 To 3
        Print #1, r.jonot(i), r.sis(i).nimi, r.sis(i).arvo
    Next i
    Print #1, "[" & r.kiinteat(2) & "]", r.vali(2).a, r.vali(2).b
    For i = 1 To 2
        For j = 0 To 2
            Print #1, r.ruudut(i, j);
        Next j
    Next i
    Print #1,
    Print #1, r.variantit(2), r.vinkki, VarType(r.vinkki)
    Print #1, UBound(r.dyn), r.dyn(2), UBound(r.dynrec), r.dynrec(1).nimi
    Print #1, r.olio.Count, r.olio(1)
End Sub

Sub Main()
    Dim paikal As TIso
    Open "out.txt" For Output As #1
    Tayta kaikki(1), 1
    Tayta kaikki(2), 2
    Tayta paikal, 3
    Tayta yksi, 4
    Nayta kaikki(2)
    Nayta paikal
    Nayta yksi
    kaikki(1) = paikal
    Nayta kaikki(1)
    Print #1, Len(paikal.otsikko), LenB(paikal.vali(1))
    Close #1
End Sub
