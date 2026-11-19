Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim s As String, v As Variant, d As Date
    Open App.Path & "\out.txt" For Output As #1
    s = "2018-05-06 13:14:15"
    d = DateValue(s)
    Print #1, CDbl(d)
    d = TimeValue(s)
    Print #1, Format$(d, "hh:nn:ss")
    v = CVDate("2001-02-03")
    Print #1, TypeName(v); CDbl(v)
    Print #1, IsDate("2018-05-06"); IsDate("nope"); IsDate(s); IsDate(1)
    d = DateSerial(2020, 1, 31)
    Print #1, Year(d); Month(d); Day(d); Weekday(d)
    v = DateAdd("m", 1, d)
    Print #1, Format$(v, "yyyy-mm-dd")
    Close #1
End Sub
