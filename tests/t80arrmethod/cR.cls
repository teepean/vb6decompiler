VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cR"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
Private k As Long

Public Function Tayta(s() As String, n As Long) As Boolean
    Dim i As Long
    k = k + 1
    n = 2 + k
    ReDim s(1 To n)
    For i = 1 To n
        s(i) = "rivi" & i * k
    Next i
    Tayta = True
End Function

Public Function Tayta2(ByVal s As String, ByVal t As String) As String
    Tayta2 = s & t
End Function
