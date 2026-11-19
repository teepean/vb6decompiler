VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "cP"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = True
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

' Properties to reach late bound, with subscripts.
Private items(5) As String
Private cells(3, 3) As Long
Private n As Long

Public Property Let Item(ByVal i As Long, ByVal v As String)
    items(i) = v
End Property

Public Property Get Item(ByVal i As Long) As String
    Item = items(i)
End Property

Public Property Let Cell(ByVal i As Long, ByVal j As Long, ByVal v As Long)
    cells(i, j) = v
End Property

Public Property Get Cell(ByVal i As Long, ByVal j As Long) As Long
    Cell = cells(i, j)
End Property

Public Sub Hand(kohde As Object, s As String)
    kohde.Item(1) = s
    Apu 1, kohde
End Sub

Public Property Get Inner() As cP
    Set Inner = New cP
End Property

Public Sub Both(ByVal which As Long)
    ' (a local of a type the callee names, given in a method of the same class)
    Dim k As Object
    On Error GoTo bad
    If which = 1 Then
        Set k = Me.Inner
    Else
        Set k = New cP
    End If
    Hand k, "oma"
    Print #1, k.Item(1); which
    Hand k, "oma 2"
    Print #1, k.Item(1); which
    Exit Sub
bad:
    Print #1, "virhe"; Err.Number
End Sub

Public Property Get Reads() As Long
    n = n + 1
    Reads = n
End Property
