Attribute VB_Name = "mM"
Option Explicit

Public gC As Currency

Function SumC(ByVal a As Currency, ByVal b As Currency, ByVal st As Currency) As Currency
    Dim c As Currency
    For c = a To b Step st
        SumC = SumC + c
    Next c
End Function

Sub Main()
    Dim c As Currency, n As Long, a As Currency, b As Currency, st As Currency, c2 As Currency
    Dim dt As Date, sg As Single
    Open App.Path & "\out.txt" For Output As #1
    For c = 1 To 3
        Print #1, c;
    Next c
    Print #1, "end"; c
    For c = 0.5 To 2 Step 0.25
        Print #1, c;
    Next c
    Print #1, "end"; c
    For c = 2 To 1 Step -0.5
        Print #1, c;
    Next c
    Print #1, "end"; c
    For c = 5 To 1
        n = n + 1
    Next c
    Print #1, n; c
    a = 1.5: b = 3: st = 0.75
    For c = a To b Step st
        Print #1, c;
    Next c
    Print #1, "end"; c
    Print #1, SumC(1, 4, 1); SumC(0.1, 0.5, 0.1); SumC(10, 0, -2.5)
    For gC = 1 To 2 Step 0.5
        Print #1, gC;
    Next gC
    Print #1, "end"; gC
    For c = 1 To 3
        For c2 = c To 2 Step 0.5
            n = n + 1
            If c2 = 1.5 Then Exit For
        Next c2
        Print #1, c; c2; n
    Next c
    For c = 1 To 10
        If c * c > 10 Then Exit For
    Next c
    Print #1, c
    n = 3
    For c = n To n * 2 Step n / 2
        Print #1, c;
    Next c
    Print #1,
    For dt = DateSerial(2020, 2, 27) To DateSerial(2020, 3, 1)
        Print #1, Format$(dt, "mm-dd");
        Print #1, " ";
    Next dt
    Print #1,
    For dt = 1.5 To 3 Step 0.5
        Print #1, CDbl(dt);
    Next dt
    Print #1,
    For sg = 0.5 To 2 Step 0.5
        Print #1, sg;
    Next sg
    Print #1, sg
    Close #1
End Sub
