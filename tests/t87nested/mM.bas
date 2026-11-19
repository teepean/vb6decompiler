Attribute VB_Name = "mM"
Option Explicit

' Records inside records, as DarwinBots has them: a fixed array of records in a module record, numbers
' and arrays in its elements, whole records copied between them.
Public Type TProb
    onko As Boolean
    arvot(5) As Single
    keski(5) As Single
    leveys As Integer
    syvyys As Integer
End Type

Public Type TLaji
    iho(3) As Integer
    nimi As String
    polku As String
    maara As Integer
    kasvi As Boolean
    vari As Long
    yla As Single
    ala As Single
    mu As TProb
    toinen As String
    viim As Boolean
End Type

Public Type TKaikki
    otsikko As String
    kierros As Long
    lkm As Integer
    lajit(4) As TLaji
    koko As Integer
    kulut(3) As Single
    leveys As Long
End Type

Public Type TBlokki
    tipo As Integer
    arvo As Integer
End Type

Public Type TBlokki3
    tipo As Integer
    arvo As Integer
    sopii As Integer
End Type

Public Type TSide
    portti As Integer
    osoitin As Integer
    kohde As Integer
    pit As Single
    kiinni As Boolean
    kayt As Boolean
    tyyppi As Byte
End Type

Public Type TVek
    x As Single
    y As Single
End Type

Public Type TOlento
    elossa As Boolean
    r As Single
    paikka As TVek
    siteet(3) As TSide
    muisti(7) As Integer
    dna() As TBlokki
    mu As TProb
    nimi As String
    siemen() As TBlokki
End Type

' Only numbers: no descriptor at all, [i * 4 + address] for an element
Private Type TPino
    arvo(10) As Long
    paikka As Integer
End Type

Public Type TSolu
    x As Integer
    y As Integer
End Type

Public Type TLokero
    arr() As Long
    koko As Integer
    vier(3) As TSolu
End Type

Public Type TBitit
    bitti(7) As Boolean
End Type

Public K As TKaikki
Private Pino As TPino
Public Lokerot() As TLokero
Public Oliot() As TOlento
Public Tilat() As TSide2

Public Type TSide2
    a As Byte
    b As Integer
    c As Integer
    d As Boolean
    e As Boolean
End Type

Private Sub PinoLisaa(ByVal x As Long)
    Dim a As Integer
    If Pino.paikka >= 10 Then
        For a = 0 To 8
            Pino.arvo(a) = Pino.arvo(a + 1)
        Next a
        Pino.paikka = 9
    End If
    Pino.arvo(Pino.paikka) = x
    Pino.paikka = Pino.paikka + 1
End Sub

Private Function PinoOta() As Long
    Pino.paikka = Pino.paikka - 1
    If Pino.paikka < 0 Then
        Pino.paikka = 0
        Pino.arvo(0) = 0
    End If
    PinoOta = Pino.arvo(Pino.paikka)
End Function

Private Sub Lisaa(n As Integer)
    n = n + 1
End Sub

Private Sub LisaaS(s As String)
    s = s & "+"
End Sub

Private Sub Puolita(x As Single)
    x = x / 2
End Sub

Private Function Pituus(d() As TBlokki) As Long
    Dim i As Long
    For i = 1 To UBound(d)
        If d(i).tipo = 10 And d(i).arvo = 1 Then Exit For
    Next i
    Pituus = i
End Function

Private Sub Poista(d() As TBlokki, ByVal k1 As Long)
    Dim i As Long
    For i = k1 To UBound(d) - 1
        d(i) = d(i + 1)
    Next i
    ReDim Preserve d(UBound(d) - 1)
    If Pituus(d) = 0 Then Print #1, "tyhja"
End Sub

Private Function Rajattu(d() As TBlokki) As Boolean
    On Error GoTo pois
    Rajattu = UBound(d) >= LBound(d)
pois:
End Function

Private Function Etsi(rob() As TBlokki3, ByVal n As Integer, ByRef taso As Integer)
    Dim a As Integer
    For a = n To UBound(rob)
        If rob(a).sopii <> taso Then
            Etsi = a
            taso = rob(a).sopii
            Exit Function
        End If
    Next a
    Etsi = UBound(rob) + 1
End Function

Private Sub Risteytys(r1() As TBlokki3, r2() As TBlokki3)
    Dim t As Integer
    t = 0
    Print #1, Etsi(r1, 0, t), Etsi(r2, 0, t)
    r1(0).tipo = r2(0).arvo
End Sub

Private Function BitAnd2(a As TBitit, b As TBitit) As TBitit
    Dim i As Long
    For i = 0 To 7
        BitAnd2.bitti(i) = a.bitti(i) And b.bitti(i)
    Next i
End Function

Private Function ArvotS(p As TProb) As Single
    Dim i As Integer, s As Single
    For i = 0 To 5
        s = s + p.arvot(i) * p.keski(i)
    Next i
    ArvotS = s
End Function

Private Sub Oletus(p As TProb)
    Dim i As Integer
    p.onko = True
    For i = 0 To 5
        p.arvot(i) = i + 0.5
        p.keski(i) = 2 - i
    Next i
    p.leveys = 3
    p.syvyys = 4
End Sub

' A parameter as Variant: VB builds VT_BYREF around what it gets, an array too.
Private Sub Monta(ByRef nimet, ByRef luvut)
    Print #1, UBound(nimet), UBound(luvut, 2)
    nimet(1) = "uusi"
    luvut(1, 1) = 7.5
End Sub

Private Function Tieto(robi) As String
    Tieto = Oliot(robi).nimi
    If Tieto = "" Then Tieto = "ei nimea"
End Function

Private Sub Tyhjenna(ByVal mika As Integer, s() As String)
    Dim i As Integer
    Erase Oliot(mika).muisti
    Erase K.kulut
    For i = LBound(s) To UBound(s)
        s(i) = ""
    Next i
End Sub

Private Sub Alusta()
    Dim i As Integer, j As Integer
    K.otsikko = "koe"
    K.kierros = 5
    K.lkm = 3
    K.koko = 99
    K.leveys = 640
    For i = 0 To 4
        K.lajit(i).nimi = "laji" & i
        K.lajit(i).polku = "p" & i
        K.lajit(i).maara = i * 10
        K.lajit(i).kasvi = (i Mod 2 = 0)
        K.lajit(i).vari = i * 1000
        K.lajit(i).yla = i / 2
        K.lajit(i).ala = i * 1.5
        K.lajit(i).toinen = "t" & i
        K.lajit(i).viim = (i = 4)
        For j = 0 To 3
            K.lajit(i).iho(j) = i * 10 + j
        Next j
        Oletus K.lajit(i).mu
        K.lajit(i).mu.leveys = i
        K.lajit(i).mu.keski(i) = 100 + i
    Next i
    For i = 0 To 3
        K.kulut(i) = i * 0.25
    Next i
    ReDim Oliot(3)
    For i = 1 To 3
        With Oliot(i)
            .elossa = True
            .r = i + 0.5
            .paikka.x = i * 2
            .paikka.y = i * 3
            .nimi = IIf(i = 2, "", "o" & i)
            For j = 0 To 7
                .muisti(j) = i * j
            Next j
            ReDim .dna(4)
            For j = 0 To 4
                .dna(j).tipo = j
                .dna(j).arvo = i + j
            Next j
            .dna(3).tipo = 10
            .dna(3).arvo = 1
            For j = 0 To 3
                .siteet(j).portti = j + 1
                .siteet(j).osoitin = (i Mod 3) + 1
                .siteet(j).kohde = j
                .siteet(j).pit = j * 0.5
                .siteet(j).kiinni = (j = 1)
                .siteet(j).tyyppi = j + 40
            Next j
            Oletus .mu
        End With
    Next i
End Sub

Private Sub Tulosta()
    Dim i As Integer, j As Integer
    Print #1, K.otsikko, K.kierros, K.lkm, K.koko, K.leveys, UBound(K.lajit), LBound(K.kulut), UBound(K.kulut)
    For i = 0 To UBound(K.lajit)
        Print #1, K.lajit(i).nimi; K.lajit(i).polku; K.lajit(i).maara; K.lajit(i).kasvi; K.lajit(i).vari; K.lajit(i).yla; K.lajit(i).ala; K.lajit(i).toinen; K.lajit(i).viim
        For j = 0 To 3
            Print #1, K.lajit(i).iho(j);
        Next j
        Print #1, ArvotS(K.lajit(i).mu), K.lajit(i).mu.leveys, K.lajit(i).mu.syvyys
    Next i
    For i = 0 To 3
        Print #1, K.kulut(i);
    Next i
    Print #1,
End Sub

Private Sub Oliotulos()
    Dim i As Integer, j As Integer, s As String
    For i = 1 To UBound(Oliot)
        s = ""
        For j = 0 To 7
            s = s & Str$(Oliot(i).muisti(j))
        Next j
        Print #1, s, Tieto(i), Pituus(Oliot(i).dna), Rajattu(Oliot(i).dna), Rajattu(Oliot(i).siemen)
        With Oliot(i)
            Print #1, .elossa, .r, .paikka.x, .paikka.y
            j = 1
            Do While .siteet(j).osoitin > 0 And j < 3
                Print #1, Oliot(.siteet(j).osoitin).r, .siteet(j).portti, .siteet(j).kohde, .siteet(j).pit, .siteet(j).kiinni, .siteet(j).tyyppi, Oliot(.siteet(j).osoitin).siteet(j).kohde
                j = j + 1
            Loop
        End With
    Next i
End Sub

Private Sub Ristikko()
    Dim x As Integer, y As Integer, z As Integer, s As Long
    ReDim Lokerot(2, 3)
    For x = 0 To 2
        For y = 0 To 3
            ReDim Lokerot(x, y).arr(0)
            Lokerot(x, y).koko = 0
            For z = 0 To 3
                Lokerot(x, y).vier(z).x = -1
            Next z
            If x > 0 Then Lokerot(x, y).vier(0).x = x - 1: Lokerot(x, y).vier(0).y = y
            If y > 0 Then Lokerot(x, y).vier(1).x = x: Lokerot(x, y).vier(1).y = y - 1
        Next y
    Next x
    For x = 0 To 2
        For y = 0 To 3
            With Lokerot(x, y)
                .koko = .koko + 1
                ReDim Preserve .arr(.koko)
                .arr(.koko) = x * 10 + y
            End With
        Next y
    Next x
    For x = 0 To 2
        For y = 0 To 3
            s = s + Lokerot(x, y).arr(1) * Lokerot(x, y).koko + Lokerot(x, y).vier(0).x + UBound(Lokerot(x, y).arr)
        Next y
    Next x
    Print #1, s, UBound(Lokerot, 1), UBound(Lokerot, 2), Lokerot(1, 2).vier(1).y
End Sub

Private Sub Tiedosto()
    Dim i As Integer, f As Integer, p As String
    p = App.Path & "\t87.tmp"
    f = FreeFile
    Open p For Output As #f
    Write #f, K.kierros, False, K.lkm
    Write #f, K.lajit(1).yla, K.lajit(2).maara, K.lajit(3).nimi
    For i = 0 To 3
        Write #f, K.kulut(i)
    Next i
    Close #f
    K.kierros = 0: K.lkm = 0: K.lajit(4).yla = 0: K.lajit(4).maara = 0: K.lajit(4).nimi = ""
    Dim b As Boolean
    Open p For Input As #f
    Input #f, K.kierros, b, K.lkm
    Input #f, K.lajit(4).yla, K.lajit(4).maara, K.lajit(4).nimi
    For i = 0 To 3
        Input #f, K.kulut(3 - i)
    Next i
    Close #f
    Kill p
    Print #1, K.kierros, b, K.lkm, K.lajit(4).yla, K.lajit(4).maara, K.lajit(4).nimi, K.kulut(0), K.kulut(3)
End Sub

Public Sub Main()
    Dim i As Integer
    Open App.Path & "\out.txt" For Output As #1
    Alusta
    Tulosta
    ' whole records between arrays in records
    K.lajit(1).mu = K.lajit(2).mu
    Oliot(1).mu = K.lajit(3).mu
    K.lajit(0).mu = Oliot(2).mu
    Print #1, ArvotS(K.lajit(1).mu), ArvotS(Oliot(1).mu), ArvotS(K.lajit(0).mu)
    K.lajit(4) = K.lajit(0)
    Print #1, K.lajit(4).nimi, K.lajit(4).iho(2), K.lajit(4).mu.leveys
    Oliot(1).siteet(0) = Oliot(1).siteet(1)
    Print #1, Oliot(1).siteet(0).portti, Oliot(1).siteet(0).kiinni
    ' by reference: a field of an element, an element of an array in it
    Lisaa K.lajit(2).maara
    Lisaa K.lajit(2).iho(3)
    LisaaS K.lajit(3).nimi
    Puolita K.lajit(3).yla
    Puolita K.lajit(3).mu.arvot(2)
    Lisaa Oliot(2).muisti(4)
    Print #1, K.lajit(2).maara, K.lajit(2).iho(3), K.lajit(3).nimi, K.lajit(3).yla, K.lajit(3).mu.arvot(2), Oliot(2).muisti(4)
    Dim d() As TBlokki
    d = Oliot(1).dna
    Poista d, 2
    Print #1, UBound(d), d(2).tipo, d(2).arvo
    Oliot(2).siemen = d
    Poista Oliot(2).siemen, 1
    Print #1, Pituus(Oliot(2).siemen), Oliot(2).siemen(1).tipo
    Dim r1() As TBlokki3, r2() As TBlokki3
    ReDim r1(3): ReDim r2(3)
    r1(2).sopii = 5: r2(1).sopii = 6: r2(0).arvo = 44
    Risteytys r1, r2
    Print #1, r1(0).tipo
    Dim b1 As TBitit, b2 As TBitit, b3 As TBitit
    b1.bitti(1) = True: b1.bitti(2) = True: b2.bitti(2) = True: b2.bitti(3) = True
    b3 = BitAnd2(b1, b2)
    Print #1, b3.bitti(1), b3.bitti(2), b3.bitti(3)
    Dim nimet(2) As String, luvut(2, 3) As Single
    nimet(1) = "vanha": luvut(1, 1) = 1.5
    Monta nimet, luvut
    Print #1, nimet(1), luvut(1, 1)
    Oliotulos
    Ristikko
    Tiedosto
    For i = 1 To 15
        PinoLisaa i * 3
    Next i
    Print #1, PinoOta(), PinoOta(), Pino.paikka
    Dim c As cP
    Set c = New cP
    Dim x As Double
    For i = 1 To 25
        c.Tyonna i / 4
    Next i
    x = c.Ota() + c.Ota() * 2
    Print #1, x
    For i = 1 To 7
        x = c.Keskiarvo(i * 10)
    Next i
    Print #1, x, c.Kerrat2
    Dim s(1) As String
    s(0) = "a": s(1) = "b"
    Tyhjenna 3, s
    Print #1, K.kulut(2), Oliot(3).muisti(5), s(1) = ""
    ReDim Tilat(2)
    Tilat(1).a = 5: Tilat(1).b = 6: Tilat(1).c = 7: Tilat(1).d = True: Tilat(1).e = False
    Tilat(2) = Tilat(1)
    Print #1, Tilat(2).a, Tilat(2).b, Tilat(2).c, Tilat(2).d, Tilat(2).e
    Close #1
End Sub
