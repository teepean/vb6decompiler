Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Dim dt As Date, sg As Single, a As Date, b As Date, n As Long
    Open App.Path & "\out.txt" For Output As #1
    a = DateSerial(2020, 2, 27): b = DateSerial(2020, 3, 1)
    For dt = a To b
        Print #1, Format$(dt, "mm-dd");
        Print #1, " ";
        n = n + 1
        If n > 8 Then Exit For
    Next dt
    Print #1,
    For dt = a To a + 1 Step 0.5
        Print #1, CDbl(dt);
    Next dt
    Print #1,
    For sg = 0.5 To 2 Step 0.5
        Print #1, sg;
    Next sg
    Print #1, sg
    Close #1
End Sub
