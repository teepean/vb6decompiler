Attribute VB_Name = "mM"
Option Explicit

' Everything under On Error Resume Next: VB keeps nothing in registers from one statement to the next, and a
' temporary's slot goes on to hold other things.  A wrong type or a lost Set raises an error nothing shows; only
' the values can tell.

Type TP
    a As Long
    s As String
    v(1 To 3) As Integer
    d As Double
End Type

Public gP As TP
Public gArr(1 To 5) As Long
Public gS As String
Public gD As Double
Public gV As Variant
Public gO As Object
Public gC As cT
Public gT(1 To 2) As TP

Function Inner(ByVal k As Long) As Long
    Inner = 10 \ k
End Function

Function OrnFun(ByVal k As Long) As String
    On Error Resume Next
    OrnFun = "start"
    OrnFun = "v" & CStr(10 \ k)
End Function

Sub Numbers()
    Dim i As Long, n As Integer, b As Byte, d As Double, z As Long, s As Single, c As Currency
    On Error Resume Next
    i = 5
    i = i / z
    Print #1, "div"; i; Err.Number
    Err.Clear
    n = 32767
    n = n + 1
    Print #1, "over"; n; Err.Number
    Err.Clear
    b = 200
    b = b + 100
    Print #1, "byte"; b; Err.Number
    Err.Clear
    d = 1.5: d = d * 2 + i
    s = d / 4
    c = d * 1.25
    Print #1, d; s; c
    gD = d + s
    gD = gD / z
    Print #1, gD; Err.Number
    Err.Clear
    i = CLng("abc")
    Print #1, "cvt"; i; Err.Number
    Err.Clear
    i = Inner(0)
    Print #1, "inner"; i; Err.Number
    Err.Clear
    Print #1, OrnFun(2); OrnFun(0)
    Print #1, Err.Number
    Err.Clear
End Sub

Sub Strings()
    Dim s As String, t As String, n As Long, f As String * 6, v As Variant
    On Error Resume Next
    s = "  hello world  "
    t = Trim$(s)
    n = Len(t)
    Print #1, t; n; InStr(t, "wor"); Left$(t, 2); Right$(t, 2); Mid$(t, 3, 2)
    t = UCase$(t) & LCase$(t)
    Mid$(t, 1, 2) = "ZZ"
    f = "abc"
    Print #1, t; "|"; f; "|"
    s = Mid$(t, -1)
    Print #1, "bad mid"; Err.Number
    Err.Clear
    s = String$(3, "x") & Space$(1) & Chr$(65) & Asc("B")
    Print #1, s
    v = s & 1
    v = v & 2
    Print #1, v; Len(v)
    gS = gS & "a"
    gS = gS & Replace(gS, "a", "bb")
    Print #1, gS
    t = Left$(gS, 1000000000)
    t = Space$(-1)
    Print #1, Err.Number; Len(t)
    Err.Clear
End Sub

Sub Arrays()
    Dim a(1 To 3) As Long, d() As String, i As Long, m(1 To 2, 1 To 2) As Integer, v As Variant
    On Error Resume Next
    For i = 1 To 5
        a(i) = i * 2
        gArr(i) = a(i) + 1
    Next
    Print #1, a(1); a(3); gArr(3); gArr(5); Err.Number
    Err.Clear
    i = 9
    Print #1, a(i); "after"; Err.Number
    Err.Clear
    ReDim d(2)
    d(1) = "x"
    d(5) = "no"
    Print #1, d(1); UBound(d); Err.Number
    Err.Clear
    ReDim Preserve d(4)
    d(4) = d(1) & "y"
    Print #1, d(4); UBound(d)
    m(1, 2) = 7
    i = 3
    m(i, i) = 8
    Print #1, m(1, 2); Err.Number
    Err.Clear
    v = Array(1, "two", 3.5)
    Print #1, v(1); UBound(v); Err.Number
    Print #1, v(7)
    Print #1, Err.Number
    Err.Clear
    Erase d
    Print #1, UBound(d); Err.Number
    Err.Clear
    gP.a = 3
    gP.s = "rec"
    gP.v(2) = 9
    i = 9
    gP.v(i) = 1
    gP.d = 2.5
    gT(2) = gP
    gT(2).a = gT(2).a + gP.v(2)
    Print #1, gT(2).a; gT(2).s; gT(2).v(2); gT(2).d; Err.Number
    Err.Clear
End Sub

Sub Objects()
    Dim c As New cT, d As cT, o As Object, col As Collection, x As Variant, r As Long, i As Long
    On Error Resume Next
    c.Pub = 4
    c.Val1 = "a"
    Print #1, c.Pub; c.Val1; c.Twice(21)
    d.Pub = 1
    Print #1, "nothing"; Err.Number
    Err.Clear
    Set d = c
    d.Pub = d.Pub + 1
    Print #1, c.Pub
    With c
        .Pub = .Pub * 2
        .Val1 = .Val1 & "b"
        Print #1, .Pub; .Val1
    End With
    Set o = New cT
    o.Pub = 3
    o.Val1 = "late"
    Print #1, o.Pub; o.Val1; o.Twice(4)
    o.NoSuch 1
    Print #1, "nosuch"; Err.Number
    Err.Clear
    Set o.Next1 = c
    Print #1, o.Next1.Pub; o.Next1.Val1
    r = 1
    Print #1, c.Add(r).Add(r).Pub; r
    c.Fail
    Print #1, Err.Number; Err.Description; Err.Source
    Err.Clear
    c.InClass d
    Set gO = c
    Print #1, gO.Pub; TypeName(gO)
    Set gC = c
    Print #1, gC.Pub; gC Is c; gC Is Nothing
    Set col = New Collection
    col.Add 1, "one"
    col.Add "two", "two"
    col.Add c
    col.Add 5, "one"
    Print #1, col.Count; col("one"); col(2); Err.Number
    Err.Clear
    Print #1, col("missing"); "miss"; Err.Number
    Err.Clear
    i = 0
    For Each x In col
        i = i + 1
        Print #1, i; TypeName(x)
    Next
    Set x = Nothing
    Set c = Nothing
    Print #1, gC.Pub
    Set gC = Nothing
    Set gO = Nothing
    Print #1, gC.Pub; Err.Number
    Err.Clear
End Sub

Sub Flow()
    Dim z As Long, i As Long, k As Long, s As String, v As Variant
    On Error Resume Next
    If 1 / z > 0 Then
        Print #1, "then of an error"
    Else
        Print #1, "else of an error"
    End If
    Err.Clear
    k = 2
    Select Case k
    Case 1
        Print #1, "one"
    Case 2
        i = 1 / z
        Print #1, "two"; Err.Number
    Case Else
        Print #1, "else"
    End Select
    Err.Clear
    ' (a jump table: the index in a pooled slot)
    For i = 0 To 5
        Select Case i + 1
        Case 1
            Print #1, "case one"
        Case 2
            Print #1, "case two"
        Case 3
            Print #1, "case three"
        Case 4
            Print #1, "case four"
        Case 5, 6
            Print #1, "case five"; i
        Case Else
            Print #1, "case else"
        End Select
    Next
    Select Case 1 / z
    Case 1
        Print #1, "sel one"
    Case Else
        Print #1, "sel else"
    End Select
    Print #1, "after sel"; Err.Number
    Err.Clear
    For i = 1 To 3
        If i = 2 Then GoTo skip
        k = k + i
skip:
    Next
    Print #1, i; k
    i = 0
    Do While i < 3
        i = i + 1
        k = k / z
    Loop
    Print #1, i; k; Err.Number
    Err.Clear
    Do
        i = i - 1
        If i < 1 Then Exit Do
    Loop Until 1 / z > 0
    Print #1, "loop"; i; Err.Number
    Err.Clear
    z = 2
    For i = 1 To 4 / z
        Print #1, "for body"; i
        If i > 1 Then Exit For
    Next
    Print #1, "for"; i; Err.Number
    Err.Clear
    z = 0
    For v = 1 To 3
        Print #1, "var for"; v
    Next
    For v = 2.5 To 0 Step -1
        Print #1, "var down"; v
    Next
    Dim arr(1 To 2) As Variant
    arr(1) = "p": arr(2) = 7
    For Each v In arr
        Print #1, "each"; v
    Next
    v = IIf(k > 100, "big", "small")
    Print #1, v
    On Error GoTo 0
    On Error Resume Next
    s = CStr(1 / z)
    Print #1, s; "|"; Err.Number
    Err.Clear
    Err.Raise 77, "here", "own"
    Print #1, Err.Number; Err.Source; Err.Description
    Err.Clear
    Print #1, Error$(11)
End Sub

Sub Files()
    Dim n As Integer, s As String, m As Integer, l As Long
    On Error Resume Next
    n = FreeFile
    Open App.Path & "\nosuch\x.txt" For Input As #n
    Print #1, "open"; Err.Number
    Err.Clear
    Line Input #n, s
    Print #1, "read"; Err.Number
    Err.Clear
    Close #n
    Open App.Path & "\tmp.txt" For Output As #n
    Print #n, "line one"
    Print #n, 2; "x"
    Close #n
    m = FreeFile
    Open App.Path & "\tmp.txt" For Input As #m
    Line Input #m, s
    Print #1, s; EOF(m); LOF(m)
    Input #m, l
    Print #1, l
    Input #m, s
    Input #m, s
    Print #1, "past end"; Err.Number
    Err.Clear
    Close #m
    Print #1, Len(Dir$(App.Path & "\tmp.txt"))
    Kill App.Path & "\tmp.txt"
    Kill App.Path & "\tmp.txt"
    Print #1, "kill"; Err.Number
    Err.Clear
End Sub

Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    Numbers
    Strings
    Arrays
    Objects
    Flow
    Files
    Close #1
End Sub
