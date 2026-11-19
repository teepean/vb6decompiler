VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cK"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
Public Total As Long

Public Function Add(ByVal a As Long, Optional ByVal b As Long = 1) As Long
    Total = Total + a + b
    Add = Total
End Function

Public Sub Bump(ByVal by As Long, ByVal again As Long)
    Total = Total + by * again
End Sub
