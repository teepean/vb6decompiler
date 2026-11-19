Attribute VB_Name = "mM"
Option Explicit

Function Pick(ByVal k As Long) As String
    Dim r As String
    r = "<"
    On k GoSub One, Two, Three
    r = r & ">"
    Pick = r
    Exit Function
One:
    r = r & "one"
    Return
Two:
    r = r & "two"
    Return
Three:
    r = r & "three"
    Return
End Function

Function Jump(ByVal k As Integer) As String
    On k GoTo A, B
    Jump = "none"
    Exit Function
A:
    Jump = "A"
    Exit Function
B:
    Jump = "B"
End Function

Function Bad(ByVal k As Long) As Long
    On Error GoTo e
    On k GoTo A
    Bad = 100
    Exit Function
A:
    Bad = 1
    Exit Function
e:
    Bad = Err.Number
End Function

Sub Main()
    Dim i As Long
    Open App.Path & "\out.txt" For Output As #1
    For i = 0 To 4
        Print #1, i; Pick(i); Jump(CInt(i))
    Next i
    Print #1, Bad(1); Bad(0); Bad(-1)
    Close #1
End Sub
