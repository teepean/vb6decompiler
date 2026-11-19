VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "clsSink"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public FByte As Byte
Public FBool As Boolean
Public FInt As Integer
Public FSng As Single
Public FDbl As Double
Public FCur As Currency
Public FDate As Date
Public FVar As Variant
Public FObj As Object
Private WithEvents mSrc As clsT04
Private mCount As Long

Private Sub mSrc_EvNone()
    mCount = mCount + 1
End Sub

Private Sub mSrc_EvLong(ByVal a As Long)
    mCount = mCount + a
End Sub

Private Sub mSrc_EvAll(ByVal b As Byte, ByVal f As Boolean, ByVal i As Integer, ByVal l As Long, ByVal s As Single, ByVal d As Double, ByVal c As Currency, ByVal t As Date, ByVal st As String, ByVal o As Object, ByVal v As Variant)
    mCount = mCount + l + Len(st)
End Sub

Private Sub mSrc_EvRef(x As Long, s As String, v As Variant)
    x = 5
    s = "r"
End Sub

Public Function FormOpt(Optional ByVal a As Long = 3, Optional ByVal s As String = "q") As String
    FormOpt = s & CStr(a)
End Function

Public Sub Run()
    Set mSrc = New clsT04
    mSrc.Fire
    mCount = mCount + 1
    Set mSrc = Nothing
End Sub
