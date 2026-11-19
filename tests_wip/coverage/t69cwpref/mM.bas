Attribute VB_Name = "mM"
Option Explicit

Private Declare Function CallWindowProc Lib "user32" Alias "CallWindowProcA" (ByVal lpPrevWndFunc As Long, ByVal hWnd As Long, ByVal Msg As Long, ByVal wParam As Long, ByVal lParam As Long) As Long

Function Cb3(p As Long, ByVal b As Long, ByVal c As Long, ByVal d As Long) As Long
    p = p + b
    Cb3 = p
End Function

Function Pass(ByVal p As Long) As Long
    Pass = p
End Function

Sub Main()
    Dim x As Long, p As Long
    Open App.Path & "\out.txt" For Output As #1
    x = 10
    Print #1, Cb3(x, 7, 0, 0); x
    p = Pass(AddressOf Cb3)
    Print #1, CallWindowProc(p, VarPtr(x), 7, 0, 0); x
    Close #1
End Sub
