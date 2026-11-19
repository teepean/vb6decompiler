Attribute VB_Name = "mM"
Option Explicit

' VBA functions and statements with a runtime entry of their own: what a program calling them compiles
' to, one of each.  None of this needs a window.

Sub Dates()
    Dim v As Variant
    Print #1, MonthName(1); MonthName(2, True); WeekdayName(1); WeekdayName(2, True, vbMonday)
    Print #1, Format$(DateValue("2001-02-03"), "yyyy-mm-dd"); Format$(TimeValue("12:34:56"), "hh:nn:ss")
    v = "2001-02-03"
    Print #1, Format$(DateValue(v), "yyyy-mm-dd")
    v = "12:34:56"
    Print #1, Format$(TimeValue(v), "hh:nn:ss")
End Sub

Sub Finance()
    Dim a(3) As Double
    a(0) = -1000: a(1) = 400: a(2) = 400: a(3) = 400
    Print #1, Round(Pmt(0.1, 10, 1000), 4); Round(IPmt(0.1, 1, 10, 1000), 4); Round(PPmt(0.1, 1, 10, 1000), 4)
    Print #1, Round(FV(0.1, 10, -100), 4); Round(PV(0.1, 10, -100), 4); Round(NPer(0.1, -200, 1000), 4)
    Print #1, Round(Rate(10, -100, 500), 4); Round(NPV(0.1, a), 4); Round(IRR(a), 4); Round(MIRR(a, 0.1, 0.1), 4)
    Print #1, DDB(1000, 100, 5, 1); SLN(1000, 100, 5); SYD(1000, 100, 5, 1)
End Sub

Sub Misc()
    Dim s(3) As String, r() As String, v As Variant, c As cK, i As Integer
    s(0) = "apple": s(1) = "pear": s(2) = "plum": s(3) = "fig"
    r = Filter(s, "p")
    Print #1, UBound(r); r(0)
    r = Filter(s, "p", False)
    Print #1, UBound(r); r(0)
    Print #1, QBColor(4); Partition(5, 0, 10, 2)
    v = -7
    Print #1, Sgn(v)
    v = "ab"
    Print #1, StrConv(v, vbUpperCase)
    Print #1, InStr(2, "abcabc", "c", vbTextCompare); InStrRev("abcabc", "b"); InStrB("abc", "b")
    Print #1, StrComp("a", "B", vbTextCompare); StrComp("a", "B", vbBinaryCompare)
    v = "a"
    Print #1, StrComp(v, "B", vbTextCompare)
    Print #1, Replace("aXbXc", "X", "--"); Join(s, "+"); StrReverse("abc")
    Set c = New cK
    Print #1, CallByName(c, "Add", VbMethod, 2, 3)
    CallByName c, "Total", VbLet, 40
    Print #1, CallByName(c, "Total", VbGet)
    Beep
End Sub

Sub Settings()
    Dim ap As String, i As Long, h As Long
    ' (rtsuite runs a test's builds side by side: a key of their own)
    For i = 1 To Len(App.Path)
        h = (h * 31 + Asc(Mid$(App.Path, i, 1))) And &HFFFFF
    Next i
    ap = "vb6dec" & h
    SaveSetting ap, "t94", "k", "v1"
    Print #1, GetSetting(ap, "t94", "k", "none"); GetSetting(ap, "t94", "nok", "none")
    DeleteSetting ap, "t94"
    Print #1, GetSetting(ap, "t94", "k", "gone")
End Sub

Sub Files()
    Dim a As String, b As Variant
    Open App.Path & "\t94.txt" For Output As #2
    Print #2, "hello world"
    Close #2
    FileCopy App.Path & "\t94.txt", App.Path & "\t94b.txt"
    Open App.Path & "\t94b.txt" For Input As #2
    a = Input$(3, #2)
    b = Input(2, #2)
    Print #1, a; b
    Close #2
    Open App.Path & "\t94b.txt" For Binary As #2
    Lock #2
    Unlock #2
    Lock #2, 1 To 3
    Unlock #2, 1 To 3
    Close #2
    Kill App.Path & "\t94.txt"
    Kill App.Path & "\t94b.txt"
End Sub

Sub Mids()
    Dim s As String, v As Variant
    s = "abcdef"
    Mid$(s, 2, 2) = "XY"
    Print #1, s
    v = "abcdef"
    Mid(v, 3) = "ZZ"
    Print #1, v
    MidB$(s, 1, 2) = "Q"
    Print #1, s
End Sub

Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    Dates
    Finance
    Misc
    Settings
    Files
    Mids
    Close #1
End Sub
