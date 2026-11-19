Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim m As Long, s As String, f As Boolean, w As VbDayOfWeek
    Open App.Path & "\out.txt" For Output As #1
    Print #1, MonthName(3)
    Print #1, MonthName(3, True)
    Print #1, MonthName(12, False)
    m = 7: f = True
    s = MonthName(m)
    Print #1, s; Len(MonthName(m, True)); MonthName(m + 1, f)
    Print #1, WeekdayName(3)
    Print #1, WeekdayName(3, True)
    Print #1, WeekdayName(3, False, vbMonday)
    w = vbSunday
    Print #1, WeekdayName(m, True, w)
    Close #1
End Sub
