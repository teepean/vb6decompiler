VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
  Persistable = 0  'NotPersistable
  DataBindingBehavior = 0  'vbNone
  DataSourceBehavior  = 0  'vbNone
  MTSTransactionMode  = 0  'NotAnMTSObject
END
Attribute VB_Name = "clsBag"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Private mItems As Collection
Private mTag As String

Private Sub Class_Initialize()
    Set mItems = New Collection
End Sub

Public Property Get Tag() As String
Attribute Tag.VB_UserMemId = 0
    Tag = mTag
End Property

Public Property Let Tag(ByVal s As String)
    mTag = s
End Property

Public Sub Add(ByVal sh As IShape, Optional ByVal key As String)
    If Len(key) > 0 Then
        mItems.Add sh, key
    Else
        mItems.Add sh
    End If
End Sub

Public Property Get Item(ByVal i As Variant) As IShape
    Set Item = mItems(i)
End Property

Public Property Get Count() As Long
    Count = mItems.Count
End Property

Public Function Total() As Double
    Dim sh As IShape
    Dim v As Variant
    For Each v In mItems
        Set sh = v
        Total = Total + sh.Area
    Next v
End Function

Friend Function Names() As String
    Dim i As Long
    For i = 1 To mItems.Count
        Names = Names & Item(i).Name & ";"
    Next i
End Function
