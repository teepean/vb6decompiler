VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "clsSquare"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit
Implements IShape

Public Side As Double

Private Function IShape_Area() As Double
    IShape_Area = Side * Side
End Function

Private Property Get IShape_Name() As String
    IShape_Name = "square"
End Property

Public Function Grow(ByVal k As Double) As clsSquare
    Dim s As clsSquare
    Set s = New clsSquare
    s.Side = Side * k
    Set Grow = s
End Function
