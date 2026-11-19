Attribute VB_Name = "mM"
Option Explicit

Private Type TKaks
    a(0 To 10) As Long
    b(0 To 10) As Long
End Type


' A procedure with no error handler, strings or objects has no ebp frame: its one temporary
' is reserved by push ecx, and a literal for a ByRef parameter is stored there just before
' the call, through esp (a real program read its records into the wrong slot).
Public gt(0 To 10) As Long
Public gn As Long
Public Type TH
    isa As Long
    aiti As Long
End Type
Public gr() As TH
Public gv(0 To 5) As Long
Public gs() As String

Sub Lue(x As Long, y As Integer)
    gt(y) = x
    gn = gn + 1
End Sub

Sub Ketju(a As Long)
    gt(1) = 0
    gt(2) = 0
    Lue a, 5
    If gt(5) > 0 Then
        Lue gt(5), 6
        If gt(6) > 1 Then
            Lue gn, 7
        End If
    End If
End Sub

' Read in the error handler only: nothing in the graph reads these stores.
Sub Yrita()
    Dim n As Integer
    On Error GoTo H
    Error 55
    Print #1, "valmis", n
    Exit Sub
H:
    If Err.Number = 55 And n < 3 Then
        n = n + 1
        Resume
    End If
    Print #1, "luovutti", n
    Resume Next
End Sub

Sub Vaiheet()
    Dim v As Integer
    On Error GoTo H
    v = 1
    Print #1, gt(5)
    v = -1
    Error 5
    v = 2
    Exit Sub
H:
    If v = -1 Then
        Print #1, "vaihe -1"
    Else
        Print #1, "vaihe", v
    End If
End Sub

' A late bound call with ByRef Variant parameters copies them with __vbaVargVar; a Double in
' a Variant is built tag first, its value computed after.
Sub Myohaan(o As Object, a As Variant, b As Variant, k As Long)
    o.Kaksi a, b
    o.Kaksi b, k / 8#
End Sub

' The copy of a record's field: the parameter must not follow the record when it is read over.
Sub Hae(h As Long)
    gr(5).isa = gv(h)
End Sub

Sub Puu(h As Long, d As Integer)
    Dim x As Long
    Print #1, "puu", d, h
    Hae h
    If gr(5).isa > 0 Then
        x = gr(5).isa
        Puu x, d + 1
    End If
    Print #1, "takaisin", d, h
End Sub

' A ByRef Variant copied dword by dword to a late bound property is no 16 byte record, and
' a Single passed to it (by a VT_BYREF Variant) with a Variant right above it are no record
' either.  A Single For loop's limit is only moved: its bits must not stay a Long.
Sub Kohta(o As Object, x As Variant)
    o.Paikka = x
End Sub

Sub Ruudukko(o As Object)
    Dim k As Single
    Dim m As Variant
    For k = 6 To 30 Step 6
        m = k * 2
        Kohta o, k
        Kohta o, m
    Next k
End Sub

' A ByRef Variant parameter given a variable builds a VT_BYREF Variant: tag 4008h, 4009h,
' 4002h, the address 8 bytes on.  The tag must not print as the value, with an array element
' or a parameter kept in a register, and a plain 3 right below such a tag is no type word.
Sub Nayta(x As Variant)
    Print #1, "nayta", x
End Sub

Sub Vie(o As Variant)
    o.Kaksi 1, 2
End Sub

Sub Kolme(a As Long, b As Long, c As Long, d As Variant)
    Print #1, "kolme", a, b, c, d
End Sub

Sub Viitteet(o As Object, s As String, n As Integer)
    Dim k As Integer
    k = 3
    If n > 0 Then
        Nayta gs(k)
        Nayta gs(1)
    Else
        Nayta s
    End If
    Vie o
    If n > 1 Then
        Vie o
        Nayta s
    End If
    Kolme 3, 3, 3, n
    Kolme CLng(n), 3, 3, n
End Sub

' Arrays inside a local record: [ebp + i * 4 - N], the record moved whole by Put and Get
' (a real program came out as (v0 + v24 * 4).f-25C = 0).
Private Sub Kaks()
    Dim r As TKaks, i As Long
    For i = 1 To 10
        r.a(i) = 0
        r.b(i) = i * 2
    Next i
    r.a(4) = 44
    Open "kaks.dat" For Random As #4 Len = 88
    Put #4, 1, r
    r.a(4) = 0: r.b(3) = 0
    Get #4, 1, r
    Close #4
    Print #1, "kaks", r.a(4), r.b(3), r.b(10)
    Kill "kaks.dat"
End Sub

Sub Main()
    Dim i As Integer
    Dim c As New cK
    ReDim gr(0 To 9)
    gv(1) = 2: gv(2) = 3: gv(3) = 0
    Open "out.txt" For Output As #1
    Puu 1, 0
    Close #1
    Open "out.txt" For Append As #1
    Ketju 3
    Yrita
    Vaiheet
    Myohaan c, 12, "kuusi", 20
    Ruudukko c
    ReDim gs(0 To 5)
    gs(1) = "yksi": gs(3) = "kolme"
    Viitteet c, "sana", 2
    Viitteet c, "toinen", 0
    c.Taulu 2
    c.Puu
    Kaks
    For i = 0 To 10
        Print #1, i, gt(i)
    Next i
    Print #1, gn
    Close #1
End Sub
