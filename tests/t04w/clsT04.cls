VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "clsT04"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public Event EvNone()
Public Event EvLong(ByVal a As Long)
Public Event EvAll(ByVal b As Byte, ByVal f As Boolean, ByVal i As Integer, ByVal l As Long, ByVal s As Single, ByVal d As Double, ByVal c As Currency, ByVal t As Date, ByVal st As String, ByVal o As Object, ByVal v As Variant)
Public Event EvRef(x As Long, s As String, v As Variant)

Public VByte As Byte
Public VBool As Boolean
Public VInt As Integer
Public VLong As Long
Public VSng As Single
Public VDbl As Double
Public VCur As Currency
Public VDate As Date
Public VStr As String
Public VObj As Object
Public VVar As Variant
Public VSelf As clsT04
Private mHidden As Long
Private mItems(1 To 4) As String

Public Sub ByVals(ByVal b As Byte, ByVal f As Boolean, ByVal i As Integer, ByVal l As Long, ByVal s As Single, ByVal d As Double, ByVal c As Currency, ByVal t As Date, ByVal st As String, ByVal o As Object, ByVal v As Variant, ByVal k As clsT04)
    mHidden = b + i + l
End Sub

Public Sub ByRefs(b As Byte, f As Boolean, i As Integer, l As Long, s As Single, d As Double, c As Currency, t As Date, st As String, o As Object, v As Variant, k As clsT04)
    l = l + 1
End Sub

Public Sub Arrays(a() As Long, s() As String, v() As Variant)
    a(LBound(a)) = UBound(s)
End Sub

Public Sub Optionals(Optional ByVal a As Long = 5, Optional ByVal b As Integer = -2, Optional ByVal s As String = "def", Optional ByVal d As Double = 2.5, Optional ByVal f As Boolean = True, Optional v As Variant, Optional ByVal c As Currency = 1.25, Optional ByVal t As Date = #1/2/2000#, Optional ByVal y As Byte = 7, Optional ByVal n As Single = 0.5, Optional ByVal z As Long)
    If IsMissing(v) Then mHidden = a + z
End Sub

Public Sub Params(ByVal first As Long, ParamArray rest() As Variant)
    mHidden = first + UBound(rest)
End Sub

Public Function RetByte() As Byte
    RetByte = 1
End Function

Public Function RetBool() As Boolean
    RetBool = True
End Function

Public Function RetInt() As Integer
    RetInt = 2
End Function

Public Function RetLong() As Long
    RetLong = 3
End Function

Public Function RetSng() As Single
    RetSng = 4.5
End Function

Public Function RetDbl() As Double
    RetDbl = 5.5
End Function

Public Function RetCur() As Currency
    RetCur = 6.5
End Function

Public Function RetDate() As Date
    RetDate = #3/4/2001#
End Function

Public Function RetStr() As String
    RetStr = "str"
End Function

Public Function RetObj() As Object
    Set RetObj = Me
End Function

Public Function RetVar() As Variant
    RetVar = 9
End Function

Public Function RetSelf() As clsT04
    Set RetSelf = Me
End Function

Public Function RetArr() As Long()
    Dim a() As Long
    ReDim a(1)
    a(1) = 10
    RetArr = a
End Function

Public Property Get PropVar() As Variant
    PropVar = VVar
End Property

Public Property Let PropVar(ByVal v As Variant)
    VVar = v
End Property

Public Property Set PropVar(ByVal v As Variant)
    Set VVar = v
End Property

Public Property Get Item(ByVal i As Long) As String
    Item = mItems(i)
End Property

Public Property Let Item(ByVal i As Long, ByVal s As String)
    mItems(i) = s
End Property

Friend Function FriendUdt(u As TPair) As Long
    FriendUdt = u.a + u.b
End Function

Public Sub Fire()
    Dim x As Long, s As String, v As Variant
    RaiseEvent EvNone
    RaiseEvent EvLong(42)
    RaiseEvent EvAll(1, True, 2, 3, 4.5, 5.5, 6.5, #3/4/2001#, "s", Me, 7)
    RaiseEvent EvRef(x, s, v)
    mHidden = x + Len(s)
End Sub

Private Sub Class_Initialize()
    VLong = 100
End Sub
