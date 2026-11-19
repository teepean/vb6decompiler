VERSION 5.00
Begin VB.Form Form1
   Caption         =   "Form1"
   ClientHeight    =   1000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   2000
   LinkTopic       =   "Form1"
   ScaleHeight     =   1000
   ScaleWidth      =   2000
   StartUpPosition =   3
   Begin VB.ListBox lst
      Height          =   300
      Left            =   100
      TabIndex        =   0
      Top             =   100
      Width           =   1000
   End
   Begin VB.Timer tmr
      Enabled         =   0   'False
      Interval        =   1000
      Left            =   1200
      Top             =   100
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

' With On Error Resume Next VB keeps no register over a statement: temporaries in frame slots, used again for other
' objects.  What goes wrong there says nothing at compile time and nothing at run time: the error is resumed from.
Private Sub Hold(ByVal n As Byte)
    Print #1, "hold"; n
End Sub

Private Function Named() As String
    Named = "n:" & Me.Caption
End Function

' another form's implicit instance, App, and the form's own procedure, by turns
Public Sub OnOtherForm()
    Dim old As String
    On Error Resume Next
    old = Form2.Caption
    Form2.Tag = "first"
    Form2.Caption = "changed"
    Print #1, "old "; old; ", now "; Form2.Caption; ", "; Form2.Tag
    Print #1, App.Title
    Hold 1
    Print #1, App.Major & "." & App.Minor
    Hold 5
    Print #1, Named()
    Print #1, Form2.Caption
    Print #1, LCase$(App.EXEName)
    Form2.Caption = old
    Unload Form2
End Sub

' a control's collection in a slot, App beside it, a number in the same slot later
Public Sub OnList()
    Dim i As Long, n As Long, p As String
    On Error Resume Next
    lst.Clear
    If lst.ListCount = 0 Then
        Print #1, "empty"
        tmr.Enabled = True
    End If
    lst.AddItem "one"
    lst.AddItem "two"
    p = LCase$(App.EXEName) & "\x"
    Print #1, p
    n = lst.ListCount
    Print #1, n
    For i = 0 To lst.ListCount - 1
        Print #1, LCase$(App.EXEName); i; lst.List(i)
    Next
    tmr.Enabled = False
    Print #1, tmr.Enabled
End Sub

' an object's method result into a Variant, handed over between temporaries: the Set must stay
Public Sub OnVariantObject()
    Dim v As Variant
    On Error Resume Next
    Set v = Me.Controls.Item("lst")
    v.Tag = "tagged"
    Print #1, lst.Tag; Err.Number
    Set v = Nothing
End Sub

' the error itself still goes by
Public Sub OnError()
    Dim o As Object, s As String
    On Error Resume Next
    s = o.Name
    Print #1, Err.Number
    Err.Clear
    Set o = Form2
    s = o.Caption
    Print #1, Err.Number; s
    Set o = Nothing
    Unload Form2
End Sub
