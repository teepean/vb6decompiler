Attribute VB_Name = "mM"
Option Explicit

Sub Show(a() As String)
    Print #1, LBound(a); UBound(a); Join(a, ",")
End Sub

Sub Main()
    Dim a(4) As String, r() As String, v As Variant, i As Long, n As Long, s As String
    Dim m As String, inc As Boolean, va(2) As Variant
    Open App.Path & "\out.txt" For Output As #1
    a(0) = "apple": a(1) = "Banana": a(2) = "cherry": a(3) = "pineapple": a(4) = "date"
    r = Filter(a, "app")
    Show r
    r = Filter(a, "app", False)
    Show r
    r = Filter(a, "AN", True, vbTextCompare)
    Show r
    r = Filter(a, "AN", True, vbBinaryCompare)
    Print #1, UBound(r)
    m = "e": inc = True
    v = Filter(a, m, inc)
    Print #1, TypeName(v); UBound(v); v(0)
    Print #1, Join(Filter(Split("x1 y2 x3", " "), "x"), "+")
    Print #1, UBound(Filter(a, "zzz"))
    va(0) = "one": va(1) = "two": va(2) = "three"
    v = va
    Print #1, Join(Filter(v, "t"), "/")
    Print #1, Partition(5, 0, 100, 10)
    Print #1, Partition(55, 0, 99, 5)
    Print #1, Partition(-1, 0, 99, 5)
    Print #1, Partition(100, 0, 99, 5)
    n = 42: i = 7
    s = Partition(n, 1, 1000, i)
    Print #1, s
    v = 42
    Print #1, Partition(v, 0, 50, v)
    For i = 0 To 15
        Print #1, QBColor(i);
    Next i
    Print #1,
    n = QBColor(9) And &HFF
    Print #1, n
    v = 4
    Print #1, QBColor(v) + 1
    Beep
    Print #1, "beep passed"
    On Error Resume Next
    n = QBColor(16)
    Print #1, Err.Number
    Close #1
End Sub
