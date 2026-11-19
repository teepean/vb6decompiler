Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim d As Double, v As Variant, c As Currency, s As String, dg As Long, dt As Date
    Open App.Path & "\out.txt" For Output As #1
    d = 1234.567: v = -0.5: c = 99.5: dg = 3
    Print #1, FormatCurrency(1234.5)
    Print #1, FormatCurrency(d, 1)
    Print #1, FormatCurrency(v, 2, vbTrue, vbTrue, vbFalse)
    Print #1, FormatCurrency(c, , vbFalse)
    s = FormatCurrency(d, dg)
    Print #1, s
    Print #1, FormatNumber(1234.5)
    Print #1, FormatNumber(d, 1)
    Print #1, FormatNumber(v, 2, vbFalse, vbTrue, vbFalse)
    Print #1, FormatNumber(d, , , , vbFalse)
    s = FormatNumber(c, dg) & "!"
    Print #1, s
    Print #1, FormatPercent(0.25)
    Print #1, FormatPercent(d, 1)
    Print #1, FormatPercent(v, 2, vbFalse, vbTrue, vbTrue)
    s = FormatPercent(0.125, dg)
    Print #1, s
    dt = DateSerial(2018, 5, 6) + TimeSerial(13, 14, 15)
    Print #1, FormatDateTime(dt)
    Print #1, FormatDateTime(dt, vbGeneralDate)
    Print #1, FormatDateTime(dt, vbLongDate)
    Print #1, FormatDateTime(dt, vbShortDate)
    Print #1, FormatDateTime(dt, vbLongTime)
    Print #1, FormatDateTime(dt, vbShortTime)
    v = dt
    s = FormatDateTime(v, 2)
    Print #1, s
    dg = 4
    Print #1, FormatDateTime(CDbl(dt), dg)
    Close #1
End Sub
