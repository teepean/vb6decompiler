Attribute VB_Name = "modT06"
Option Explicit

Public Type TRec
    Id As Long
    Code As String * 4
    Score As Double
End Type

Public gLog As String

Public Function Compound(ByVal a As Long, ByVal b As Long, ByVal s As String) As Long
    Dim r As Long
    If a > 0 And b > 0 Then r = r + 1
    If a > 10 Or b > 10 Then r = r + 2
    If (a = 1 Or a = 2) And Len(s) > 2 Then r = r + 4
    If Not (a < b) Then r = r + 8
    If s Like "a*" Then r = r + 16
    If StrComp(s, "ABC", vbTextCompare) = 0 Then r = r + 32
    If s > "abb" Then r = r + 64
    Compound = r
End Function

Public Function UntilLoop(ByVal n As Long) As Long
    Dim k As Long
    Do Until n <= 1
        If n Mod 2 = 0 Then
            n = n \ 2
        Else
            n = 3 * n + 1
        End If
        k = k + 1
    Loop
    UntilLoop = k
End Function

Public Function DynArrays() As String
    Dim a() As Long
    Dim i As Long
    Dim t As Long
    ReDim a(1 To 3)
    For i = 1 To 3
        a(i) = i * 10
    Next i
    ReDim Preserve a(1 To 5)
    a(5) = 7
    For i = LBound(a) To UBound(a)
        t = t + a(i)
    Next i
    DynArrays = CStr(t) & ":" & CStr(UBound(a))
    Erase a
End Function

Public Function SplitJoin(ByVal s As String) As String
    Dim parts() As String
    Dim i As Long
    parts = Split(s, ",")
    For i = 0 To UBound(parts)
        parts(i) = UCase$(parts(i))
    Next i
    SplitJoin = Join(parts, "-") & CStr(UBound(parts) + 1)
End Function

Public Function MathFuncs(ByVal x As Double) As String
    Dim s As String
    s = CStr(Int(x)) & CStr(Fix(-x)) & CStr(Abs(-3)) & CStr(Sgn(-x))
    s = s & CStr(Sqr(16)) & CStr(Round(x, 1)) & CStr(CInt(2.5)) & CStr(CLng(3.5))
    s = s & Hex$(255) & Oct$(8) & CStr(Val("12abc")) & CStr(5 Mod 3) & CStr(2 ^ 10)
    MathFuncs = s
End Function

Public Function StrMore(ByVal s As String) As String
    Dim t As String
    t = s
    Mid$(t, 3, 2) = "XY"
    StrMore = t & CStr(InStrRev(s, "a")) & StrReverse(s) & Space$(2) & LTrim$("  q")
End Function

Public Function Choices(ByVal n As Long) As String
    Choices = IIf(n > 2, "big", "small") & Choose(n, "one", "two", "three")
End Function

Public Function Dates() As String
    Dim d As Date
    d = DateSerial(2001, 3, 4)
    d = DateAdd("d", 30, d)
    Dates = CStr(Year(d)) & "/" & CStr(Month(d)) & "/" & CStr(Day(d)) & CStr(Weekday(d))
End Function

Public Function MakeRec(ByVal id As Long) As TRec
    Dim r As TRec
    r.Id = id
    r.Code = "AB"
    r.Score = id * 1.5
    MakeRec = r
End Function

Public Sub Bump(r As TRec)
    r.Id = r.Id + 100
    r.Score = r.Score / 2
End Sub

Public Function RecTest() As String
    Dim r As TRec
    r = MakeRec(4)
    Bump r
    RecTest = CStr(r.Id) & "[" & r.Code & "]" & CStr(r.Score)
End Function

Public Sub RunAll6()
    gLog = CStr(Compound(1, 2, "abc")) & "," & CStr(Compound(11, 0, "x"))
    gLog = gLog & "," & CStr(UntilLoop(27)) & "," & DynArrays() & "," & SplitJoin("a,bb,c")
    gLog = gLog & "," & MathFuncs(2.75) & "," & StrMore("banana") & "," & Choices(3)
    gLog = gLog & "," & Dates() & "," & RecTest()
End Sub
