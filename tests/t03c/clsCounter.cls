VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "clsCounter"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Public Event Changed(ByVal NewValue As Long)

Private mValue As Long
Private mName As String
Private mTag As Object

Public Property Get Value() As Long
    Value = mValue
End Property

Public Property Let Value(ByVal v As Long)
    mValue = v
    RaiseEvent Changed(v)
End Property

Public Property Get Name() As String
    Name = mName
End Property

Public Property Let Name(ByVal s As String)
    mName = s
End Property

Public Property Get Tag() As Object
    Set Tag = mTag
End Property

Public Property Set Tag(ByVal o As Object)
    Set mTag = o
End Property

Public Sub Increment(Optional ByVal By As Long = 1)
    Value = mValue + By
End Sub

Public Function Describe() As String
    Describe = mName & "=" & CStr(mValue)
End Function

Private Sub Class_Initialize()
    mValue = 100
End Sub

Private Sub Class_Terminate()
    Set mTag = Nothing
End Sub
