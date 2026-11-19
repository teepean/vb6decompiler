VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cL"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
Public Tag As String

Public Function Cnt() As Long
    L = L & "[" & Tag & ".Cnt] "
    Cnt = 1
End Function

Public Function Me2() As Variant
    L = L & "[" & Tag & ".Me2] "
    Set Me2 = Me
End Function
