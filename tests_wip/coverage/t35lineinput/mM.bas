Attribute VB_Name = "mM"
Option Explicit

Type TRec
    a As Long
    s As String
    fx As String * 6
End Type

Public gLine As String

Function CountLines(ByVal p As String) As Long
    Dim fn As Integer, t As String
    fn = FreeFile
    Open p For Input As #fn
    Do While Not EOF(fn)
        Line Input #fn, t
        CountLines = CountLines + 1
    Loop
    Close #fn
End Function

Sub ReadInto(ByVal fn As Integer, s As String)
    Line Input #fn, s
End Sub

Sub Main()
    Dim p As String, s As String, v As Variant, arr(2) As String, r As TRec
    Dim fx As String * 4, fn As Integer, fl As Long, h As cH, va(1) As Variant
    Dim tot As Long
    Open App.Path & "\out.txt" For Output As #1
    p = App.Path & "\in.tmp"
    Open p For Output As #2
    Print #2, "first line"
    Print #2, ""
    Print #2, "  spaced, with ""quote"" and comma  "
    Print #2, "4"
    Print #2, "array0"
    Print #2, "array1"
    Print #2, "udt string"
    Print #2, "fixed-string-long"
    Print #2, "fix4long"
    Print #2, "variant line"
    Print #2, "vararr"
    Print #2, "global"
    Print #2, "byref"
    Print #2, "class"
    Print #2, "last";
    Close #2
    Print #1, CountLines(p)
    fn = FreeFile
    Print #1, fn
    Open p For Input As #fn
    Line Input #fn, s
    Print #1, "[" & s & "]"
    Line Input #fn, s
    Print #1, "[" & s & "]"; Len(s)
    Line Input #fn, s
    Print #1, "[" & s & "]"
    Line Input #fn, s
    tot = CLng(s) + 1
    Print #1, tot
    Line Input #fn, arr(0)
    Line Input #fn, arr(tot - 4)
    Print #1, arr(0); arr(1)
    Line Input #fn, r.s
    Line Input #fn, r.fx
    Print #1, r.s; "["; r.fx; "]"
    Line Input #fn, fx
    Print #1, "["; fx; "]"
    Line Input #fn, v
    Print #1, v; TypeName(v)
    Line Input #fn, va(1)
    Print #1, va(1)
    Line Input #fn, gLine
    Print #1, gLine
    ReadInto fn, s
    Print #1, s
    Set h = New cH
    Print #1, h.ReadOne(fn)
    Print #1, EOF(fn)
    Line Input #fn, s
    Print #1, s; EOF(fn)
    On Error Resume Next
    Line Input #fn, s
    Print #1, Err.Number
    On Error GoTo 0
    Close #fn
    Close #1
End Sub
