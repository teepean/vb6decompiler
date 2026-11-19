Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim a As Date, b As Date, v As Variant, iv As String, n As Long, s As String
    Dim m As Long, w As VbDayOfWeek
    Open App.Path & "\out.txt" For Output As #1
    a = DateSerial(2018, 5, 6): b = DateSerial(2020, 2, 29) + TimeSerial(12, 30, 0)
    v = a
    iv = "m"
    Print #1, DateDiff("d", a, b)
    Print #1, DateDiff(iv, a, b)
    Print #1, DateDiff("yyyy", v, b)
    Print #1, DateDiff("h", a, b); DateDiff("n", a, b); DateDiff("s", a, b)
    Print #1, DateDiff("ww", a, b)
    Print #1, DateDiff("ww", a, b, vbMonday)
    Print #1, DateDiff("ww", a, b, vbMonday, vbFirstFourDays)
    n = DateDiff("q", a, b) * 2
    Print #1, n
    Print #1, DateDiff("d", "2018-01-01", "2018-03-01")
    Print #1, DatePart("yyyy", a); DatePart("q", a); DatePart("m", a); DatePart("d", a)
    Print #1, DatePart("y", a); DatePart("w", a); DatePart("ww", a)
    Print #1, DatePart("h", b); DatePart("n", b); DatePart("s", b)
    Print #1, DatePart("w", a, vbMonday)
    Print #1, DatePart("ww", a, vbMonday, vbFirstFullWeek)
    Print #1, DatePart(iv, v)
    n = DatePart("d", b) + 1
    Print #1, n
    Print #1, MonthName(3)
    Print #1, MonthName(3, True)
    Print #1, MonthName(12, False)
    m = 7
    s = MonthName(m)
    Print #1, s; Len(MonthName(m, True))
    Print #1, WeekdayName(3)
    Print #1, WeekdayName(3, True)
    Print #1, WeekdayName(3, False, vbMonday)
    w = vbSunday
    Print #1, WeekdayName(m, True, w)
    Print #1, Format$(DateAdd("m", 1, a), "yyyy-mm-dd"); " "; Format$(DateAdd(iv, -3, v), "yyyy-mm-dd")
    Close #1
End Sub
