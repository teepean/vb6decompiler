Attribute VB_Name = "mM"
Option Explicit

' A file number given to a procedure that only prints to it, in the three types a program would use.  Nothing
' there says number: it came out As Object, the caller's n = FreeFile compiled and raised error 91.
Sub OutI(f As Integer, ByVal s As String)
    Print #f, s
End Sub

Sub OutL(f As Long, s As String)
    Print #f, s; "!"
End Sub

Sub OutV(f As Variant, s As String)
    Print #f, s; "?"
End Sub

Sub OutIon(f As Integer, s As String, k As Integer)
    ' (and handed on, the way it went in the target)
    If k > 0 Then
        OutI f, s & CStr(k)
        OutIon f, s, k - 1
    End If
End Sub

Sub OutBy(ByVal f As Integer, s As String)
    Print #f, s; ".";
    Print #f, ""
End Sub

Sub WriteTo(f As Integer, a As Long, s As String)
    Write #f, a, s
End Sub

Sub Main()
    Dim a As Integer, b As Long, c As Variant, d As Integer, e As Integer
    On Error GoTo bad
    a = FreeFile
    Open App.Path & "\out.txt" For Output As #a
    OutI a, "integer"
    b = FreeFile
    Open App.Path & "\two.txt" For Output As #b
    OutL b, "long"
    Close #b
    c = FreeFile
    Open App.Path & "\three.txt" For Output As #c
    OutV c, "variant"
    Close #c
    OutIon a, "on", 3
    OutBy a, "byval"
    d = FreeFile
    Open App.Path & "\four.txt" For Output As #d
    WriteTo d, 7, "written"
    Close #d
    e = FreeFile
    Open App.Path & "\two.txt" For Input As #e
    Dim s As String
    Line Input #e, s
    Print #a, s
    Close #e
    e = FreeFile
    Open App.Path & "\three.txt" For Input As #e
    Line Input #e, s
    Print #a, s
    Close #e
    e = FreeFile
    Open App.Path & "\four.txt" For Input As #e
    Line Input #e, s
    Print #a, s
    Close #e
    Close #a
    Exit Sub
bad:
    Open App.Path & "\out.txt" For Append As #9
    Print #9, "error "; Err.Number; Err.Description
    Close #9
End Sub
