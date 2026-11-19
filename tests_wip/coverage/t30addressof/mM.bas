Attribute VB_Name = "mM"
Option Explicit

Private Declare Function CallWindowProc Lib "user32" Alias "CallWindowProcA" (ByVal lpPrevWndFunc As Long, ByVal hWnd As Long, ByVal Msg As Long, ByVal wParam As Long, ByVal lParam As Long) As Long

Public gCount As Long
Public gStr As String

' reached only through a pointer
Function Cb1(ByVal a As Long, ByVal b As Long, ByVal c As Long, ByVal d As Long) As Long
    gCount = gCount + 1
    Cb1 = a + b * 10 + c * 100 + d * 1000
End Function

Function Cb2(ByVal a As Long, ByVal b As Long, ByVal c As Long, ByVal d As Long) As Long
    Cb2 = Helper(a) - b
End Function

' only Cb2 calls this
Private Function Helper(ByVal x As Long) As Long
    gStr = gStr & "h" & x
    Helper = x * x
End Function

' a ByRef first parameter
Function Cb3(p As Long, ByVal b As Long, ByVal c As Long, ByVal d As Long) As Long
    p = p + b
    Cb3 = p
End Function

' also called directly
Function Cb4(ByVal a As Long, ByVal b As Long, ByVal c As Long, ByVal d As Long) As Long
    Cb4 = a - b - c - d
End Function

Private Function Pass(ByVal p As Long) As Long
    Pass = p
End Function

Sub Main()
    Dim p As Long, x As Long, v As Variant, t(1) As Long, i As Long
    Open App.Path & "\out.txt" For Output As #1
    Print #1, CallWindowProc(AddressOf Cb1, 1, 2, 3, 4)
    p = Pass(AddressOf Cb2)
    Print #1, CallWindowProc(p, 5, 6, 0, 0); gStr
    x = 10
    Print #1, CallWindowProc(AddressOf Cb3, VarPtr(x), 7, 0, 0); x
    Print #1, Cb4(100, 1, 2, 3); CallWindowProc(Pass(AddressOf Cb4), 100, 1, 2, 3)
    t(0) = Pass(AddressOf Cb1)
    t(1) = Pass(AddressOf Cb4)
    For i = 0 To 1
        Print #1, CallWindowProc(t(i), 9, 8, 7, 6)
    Next i
    v = Pass(AddressOf Cb1)
    Print #1, CallWindowProc(CLng(v), 0, 0, 0, 1); gCount
    Print #1, Pass(AddressOf Cb1) = t(0); Pass(AddressOf Cb1) = t(1)
    Close #1
End Sub
