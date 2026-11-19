Attribute VB_Name = "mM"
Option Explicit

Function Slurp(ByVal p As String) As String
    Dim f As Integer, t As String
    f = FreeFile
    Open p For Input As #f
    Do While Not EOF(f)
        Line Input #f, t
        Slurp = Slurp & t & "/"
    Loop
    Close #f
End Function

Sub Main()
    Dim a As String, b As String, c As Variant, d As String
    Open App.Path & "\out.txt" For Output As #1
    a = App.Path & "\src.tmp"
    b = App.Path & "\dst.tmp"
    c = App.Path & "\dst2.tmp"
    Open a For Output As #2
    Print #2, "line one"
    Print #2, "line two"
    Close #2
    FileCopy a, b
    Print #1, FileLen(b); Slurp(b)
    FileCopy b, c
    Print #1, FileLen(c); Slurp(CStr(c))
    FileCopy App.Path & "\src.tmp", App.Path & "\dst3.tmp"
    d = Dir$(App.Path & "\dst3.tmp")
    Print #1, d; FileLen(App.Path & "\" & d)
    Open a For Output As #2
    Print #2, "overwrite source"
    Close #2
    FileCopy a, b
    Print #1, Slurp(b)
    On Error Resume Next
    FileCopy App.Path & "\nosuch.tmp", b
    Print #1, Err.Number
    Print #1, Slurp(b)
    Close #1
End Sub
