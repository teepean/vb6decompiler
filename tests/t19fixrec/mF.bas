Attribute VB_Name = "mF"
Option Explicit
Public Type TF
    a As Long
    b1 As Byte
    s1 As String * 1
    s2 As String * 2
    n As Long
    b2 As Byte
    s3 As String * 3
    k As Integer
    t As String
    m As Long
End Type
Public recs(3) As TF

Sub Bump(x As Long)
    x = x * 10 + 1
End Sub

Sub Main()
    Dim r As TF, q As TF, f As Integer
    Open App.Path & "\out.txt" For Output As #1
    r.a = 7: r.b1 = 65: r.s1 = "x": r.s2 = "yz": r.n = 123
    r.b2 = 66: r.s3 = "pqr": r.k = -5: r.t = "tail": r.m = 999
    Bump r.n
    Bump r.m
    recs(2) = r
    Bump recs(2).n
    q = recs(2)
    Print #1, q.a; q.b1; "["; q.s1; "]["; q.s2; "]"; q.n; q.b2; "["; q.s3; "]"; q.k; q.t; q.m
    Print #1, "len"; Len(r); LenB(r)
    f = FreeFile
    Open App.Path & "\rec.dat" For Random As #f Len = Len(r) + 16
    Put #f, 1, recs(2)
    Get #f, 1, q
    Close #f
    Kill App.Path & "\rec.dat"
    Print #1, q.n; q.s3; q.m
    Close #1
End Sub
