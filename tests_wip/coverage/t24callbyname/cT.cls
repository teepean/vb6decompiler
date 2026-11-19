VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "cT"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public V As Long
Private mName As String
Private mObj As Object

Public Property Get Nm() As String
    Nm = mName
End Property

Public Property Let Nm(ByVal s As String)
    mName = "[" & s & "]"
End Property

Public Property Get Obj() As Object
    Set Obj = mObj
End Property

Public Property Set Obj(ByVal o As Object)
    Set mObj = o
End Property

Public Function Add(ByVal a As Long, ByVal b As Long) As Long
    Add = a + b
End Function

Public Function Opt(ByVal a As Long, Optional ByVal b As Long = 10) As Long
    Opt = a * b
End Function

Public Sub Bump()
    V = V + 1
End Sub

Public Function Cat(ParamArray p() As Variant) As String
    Dim i As Long
    For i = LBound(p) To UBound(p)
        Cat = Cat & p(i) & ","
    Next i
End Function
