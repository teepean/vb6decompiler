Attribute VB_Name = "mM"
Option Explicit

' Optimised code: what With and subscripts look like in registers and frame slots.
Public Type TVec
    x As Single
    y As Single
End Type

Public Type TTie
    port As Integer
    pnt As Integer
    k As Single
    v As TVec
    mem(1 To 3) As Integer
End Type

Public Type TGene
    tp As Integer
    val As Integer
End Type

Public Type TBot
    used As Boolean
    nrg As Single
    pos As TVec
    vel As TVec
    ties(5) As TTie
    mem(40) As Integer
    gene() As TGene
    lens() As Long
    nimi As String
End Type

Public Type TBucket
    arr() As Integer
    size As Long
End Type

Public Type TSpec
    native As Boolean
    nimi As String
    mean(3) As Single
End Type

Public Type TOpts
    n As Integer
    sp(4) As TSpec
End Type

Public bots() As TBot
Public buckets() As TBucket
Public tmp As TOpts
Public rnd1 As Long

Private Function VAdd(a As TVec, b As TVec) As TVec
    VAdd.x = a.x + b.x
    VAdd.y = a.y + b.y
End Function

Private Function VSet(ByVal x As Single, ByVal y As Single) As TVec
    VSet.x = x
    VSet.y = y
End Function

Private Function VLen2(a As TVec) As Single
    VLen2 = a.x * a.x + a.y * a.y
End Function

Private Sub Clip(p As TVec, ByVal lim As Single)
    If p.x > lim Then p.x = lim
    If p.y > lim Then p.y = lim
End Sub

Private Sub Spawn(ByVal n As Integer, x As Single, y As Single)
    bots(n).pos.x = x * 2
    bots(n).pos.y = y * 2
End Sub

Private Sub Move(ByVal n As Integer, ByVal lim As Single)
    With bots(n)
        .vel = VAdd(.vel, VSet(1, 2))
        If VLen2(.vel) > lim * lim Then
            Clip .vel, lim
        End If
        .pos = VAdd(.pos, .vel)
        .nrg = .nrg - 0.5
        Spawn n, .pos.x, .pos.y
    End With
End Sub

Private Sub Ties(ByVal n As Integer)
    Dim k As Integer, j As Integer
    With bots(n)
        For k = 0 To 5
            .ties(k).port = k + 1
            .ties(k).pnt = k * 2
            .ties(k).k = k / 2
            .ties(k).v = VSet(k, k + 1)
            For j = 1 To 3
                .ties(k).mem(j) = k * j
            Next j
        Next k
        For k = 0 To 4
            .ties(k) = .ties(k + 1)
        Next k
        .ties(5).port = -1
        For k = 1 To 5
            .mem(k + 10) = .ties(k - 1).port
        Next k
    End With
End Sub

Private Sub Genes(ByVal n As Integer, ByVal cnt As Integer)
    Dim i As Integer
    With bots(n)
        ReDim .gene(cnt)
        ReDim .lens(1 To cnt)
        For i = 0 To cnt
            .gene(i).tp = i
            .gene(i).val = i * 3
        Next i
        For i = 1 To cnt
            .lens(i) = .gene(i).val + .gene(i - 1).tp
        Next i
        ReDim Preserve .gene(cnt + 1)
        .gene(cnt + 1).tp = 7
        .gene(cnt + 1).val = -1
    End With
End Sub

Private Function CountT(g() As TGene, ByVal t As Integer) As Long
    Dim i As Integer, c As Long
    i = LBound(g)
    Do While i <= UBound(g)
        If g(i).tp = t Then
            If g(i).val >= 0 Then c = c + 1
        ElseIf g(i).tp = -t Then
            If g(i).val = 2 Then c = c - 1
        End If
        i = i + 1
    Loop
    CountT = c
End Function

Private Sub AddB(ByVal n As Integer, p As TVec)
    Dim a As Long
    With buckets(p.x, p.y)
        For a = 1 To .size
            If .arr(a) = -1 Then
                .arr(a) = n
                Exit Sub
            End If
        Next a
        ReDim Preserve .arr(.size + 3)
        .arr(.size + 1) = n
        .arr(.size + 2) = -1
        .arr(.size + 3) = -1
        .size = .size + 3
    End With
End Sub

Private Sub Listaa()
    Dim i As Integer
    For i = 0 To tmp.n - 1
        If tmp.sp(i).native Then
            Print #1, tmp.sp(i).nimi, tmp.sp(i).mean(i)
        End If
    Next i
End Sub

Private Sub MemFocus(ByVal focus As Integer)
    Dim i As Byte, s As String
    For i = 0 To 3
        s = CStr(bots(focus).mem(i + 11))
        Print #1, s; " ";
        s = CStr(bots(focus).ties(i).port + bots(focus).mem(i + 12))
        Print #1, s
    Next i
End Sub

Private Sub Kirjoita()
    Dim b As Long, a As Long, k As Byte
    b = 3
    a = rnd1 + bots(1).mem(b)
    For k = 0 To 3
        If b = 480 + k Then bots(1).ties(k).port = 9
        If b = 3 + k Then bots(1).ties(k).pnt = -1
    Next k
    bots(1).mem(b) = a + 1
    bots(1).nrg = bots(1).nrg - 0.25
End Sub

Sub Main()
    Dim v As TVec, n As Integer, i As Long, f As Integer
    Open "out.txt" For Output As #1
    ReDim bots(1 To 2)
    bots(1).nimi = "yksi"
    bots(2).nimi = "kaksi"
    bots(1).nrg = 100
    Move 1, 2
    Print #1, bots(1).pos.x, bots(1).pos.y, bots(1).vel.x, bots(1).vel.y, bots(1).nrg
    Move 1, 2
    Print #1, bots(1).pos.x, bots(1).pos.y, bots(1).vel.x, bots(1).vel.y, bots(1).nrg
    Ties 2
    For i = 0 To 5
        Print #1, bots(2).ties(i).port, bots(2).ties(i).pnt, bots(2).ties(i).k, bots(2).ties(i).v.x, bots(2).ties(i).mem(2)
    Next i
    For i = 11 To 15
        Print #1, bots(2).mem(i);
    Next i
    Print #1,
    Genes 1, 4
    For i = 0 To 5
        Print #1, bots(1).gene(i).tp, bots(1).gene(i).val
    Next i
    For i = 1 To 4
        Print #1, bots(1).lens(i)
    Next i
    Print #1, CountT(bots(1).gene, 2), CountT(bots(1).gene, 7)
    ReDim buckets(0 To 2, 1 To 3)
    v.x = 1: v.y = 2
    AddB 5, v
    AddB 6, v
    AddB 7, v
    AddB 8, v
    AddB 9, v
    For i = 1 To buckets(1, 2).size
        Print #1, buckets(1, 2).arr(i);
    Next i
    Print #1,
    tmp.n = 3
    tmp.sp(0).native = True: tmp.sp(0).nimi = "a": tmp.sp(0).mean(0) = 1.5
    tmp.sp(1).nimi = "b"
    tmp.sp(2).native = True: tmp.sp(2).nimi = "c": tmp.sp(2).mean(2) = 2.5
    Listaa
    bots(2).mem(11) = 4
    bots(2).mem(12) = 5
    MemFocus 2
    rnd1 = 2
    bots(1).mem(3) = 5
    Kirjoita
    Print #1, bots(1).mem(3), bots(1).nrg, bots(1).ties(0).pnt
    fF.Tee
    Print #1, fF.Tulos
    Unload fF
    Close #1
End Sub
