Attribute VB_Name = "mM"
Option Explicit

Type TRec
    s As String
    fx As String * 6
End Type

Sub ReadInto(ByVal fn As Integer, s As String)
    Line Input #fn, s
End Sub

Sub Main()
    Dim s As String, v As Variant, arr(2) As String, r As TRec, fx As String * 4, fn As Integer
    Dim i As Long, va As Variant
    Open App.Path & "\out.txt" For Output As #1
    fn = FreeFile
    Open App.Path & "\in.tmp" For Output As #fn
    For i = 1 To 9
        Print #fn, "line " & i & ", with ""q"""
    Next i
    Print #fn, "last";
    Close #fn
    Open App.Path & "\in.tmp" For Input As #fn
    Line Input #fn, s
    Print #1, "["; s; "]"
    Line Input #fn, arr(0)
    i = 2
    Line Input #fn, arr(i)
    Print #1, arr(0); arr(1); arr(2)
    Line Input #fn, r.s
    Line Input #fn, r.fx
    Print #1, r.s; "["; r.fx; "]"
    Line Input #fn, fx
    Print #1, "["; fx; "]"
    Line Input #fn, v
    Print #1, v; TypeName(v)
    ReadInto fn, s
    Print #1, s
    Do While Not EOF(fn)
        Line Input #fn, s
        Print #1, s
    Loop
    Close #fn
    Close #1
End Sub
