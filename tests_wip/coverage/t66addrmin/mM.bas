Attribute VB_Name = "mM"
Option Explicit

Private Declare Function CallWindowProc Lib "user32" Alias "CallWindowProcA" (ByVal lpPrevWndFunc As Long, ByVal hWnd As Long, ByVal Msg As Long, ByVal wParam As Long, ByVal lParam As Long) As Long

Function Cb(ByVal a As Long, ByVal b As Long, ByVal c As Long, ByVal d As Long) As Long
    Cb = a + b + c + d
End Function

Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    Print #1, CallWindowProc(AddressOf Cb, 1, 2, 3, 4)
    Close #1
End Sub
