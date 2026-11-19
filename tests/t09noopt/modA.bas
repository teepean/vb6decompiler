Attribute VB_Name = "modA"
Option Explicit

Public Type T24
    a As Long
    b As Long
    c As Long
    d As Long
    e As Long
    s As String
End Type

Public Type TSeq
    seq() As Byte
    p As Long
End Type

Private loc(0 To 11) As T24
Public net(0 To 3, 0 To 11) As T24
Private tbl(0 To 255) As Byte
Public pub1(1 To 10) As Long
Private cheat As TSeq
Public Log_ As String

Private Function Scr(ByVal a As Long) As Long
    Scr = ((a And 1) * 128) + (a And 2) * 2 + ((a And 128) \ 128)
End Function

Public Sub MakeSeq(c As TSeq, ByVal text As String)
    Dim i As Long
    ReDim c.seq(0 To Len(text))
    For i = 1 To Len(text)
        c.seq(i - 1) = Asc(Mid$(text, i, 1))
    Next
    c.seq(Len(text)) = &HFF
    c.p = -1
End Sub

Public Function SeqSum(c As TSeq) As Long
    Dim i As Long, t As Long
    For i = 0 To UBound(c.seq)
        t = t + c.seq(i)
    Next
    SeqSum = t + c.p
End Function

Public Sub Bump(x As T24, ByVal k As Long)
    x.a = x.a + k
    x.s = x.s & "+"
End Sub

Public Sub RunA()
    Dim i As Long, j As Long, ok As Boolean
    For i = 0 To 255
        tbl(i) = Scr(i) And &HFF
    Next
    For i = 0 To 11
        loc(i).a = i
        loc(i).c = i * 3
        loc(i).s = "L" & i
    Next
    For i = 0 To 3
        For j = 0 To 11
            LSet net(i, j) = loc(j)
            net(i, j).b = i * 100 + j
        Next
    Next
    For i = 1 To 10
        pub1(i) = i * i
    Next
    Bump loc(5), 7
    Bump net(2, 3), 9
    MakeSeq cheat, "iddt"
    ok = True
    Log_ = "tbl " & tbl(1) & " " & tbl(2) & " " & tbl(255) & vbCrLf
    Log_ = Log_ & "loc5 " & loc(5).a & " " & loc(5).c & " " & loc(5).s & vbCrLf
    Log_ = Log_ & "net23 " & net(2, 3).a & " " & net(2, 3).b & " " & net(2, 3).s & vbCrLf
    Log_ = Log_ & "seq " & SeqSum(cheat) & " " & ok & vbCrLf
End Sub
