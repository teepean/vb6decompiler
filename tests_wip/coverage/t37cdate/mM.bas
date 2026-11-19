Attribute VB_Name = "mM"
Option Explicit

Function Iso(ByVal d As Date) As String
    Iso = Format$(d, "yyyy-mm-dd hh:nn:ss")
End Function

Function NextDay(d As Variant) As Date
    NextDay = CDate(d) + 1
End Function

Sub Main()
    Dim d As Date, s As String, v As Variant, x As Double, l As Long, sg As Single, i As Integer
    Open App.Path & "\out.txt" For Output As #1
    d = CDate("2018-05-06")
    Print #1, Iso(d)
    s = "2018-05-06 13:14:15"
    d = CDate(s)
    Print #1, Iso(d)
    x = 43226.5
    d = CDate(x)
    Print #1, Iso(d)
    l = 43000
    Print #1, Iso(CDate(l))
    sg = 1000.25
    Print #1, Iso(CDate(sg))
    i = 365
    Print #1, Iso(CDate(i))
    v = "2000-02-29"
    Print #1, Iso(CDate(v))
    v = 36526.75
    Print #1, Iso(CDate(v))
    v = CDate("1999-12-31")
    Print #1, TypeName(v); VarType(v); Iso(v)
    Print #1, Iso(NextDay(v)); Iso(NextDay("2020-02-28"))
    Print #1, CDbl(CDate("1900-01-01")); CLng(CDate("2000-01-01"))
    Print #1, Year(CDate("2018-05-06")); Month(CDate(s)); Day(CDate(s)); Hour(CDate(s))
    Print #1, CDate("2018-05-06") > CDate("2018-05-05"); CDate(x) = d
    Print #1, Iso(DateAdd("d", 3, CDate(s)))
    Print #1, Iso(CDate(x) + CDate("12:00:00"))
    Print #1, Iso(CVDate("2001-02-03")); TypeName(CVDate(1))
    Print #1, Iso(DateValue("2018-05-06 13:14:15")); Iso(TimeValue("2018-05-06 13:14:15"))
    Print #1, IsDate("2018-05-06"); IsDate("nope"); IsDate(v); IsDate(1)
    Print #1, CStr(CDate(0.5)) <> ""; Len(CStr(CDate(x))) > 0
    On Error Resume Next
    d = CDate("nope")
    Print #1, Err.Number
    Err.Clear
    d = CDate(1E+20)
    Print #1, Err.Number
    Close #1
End Sub
