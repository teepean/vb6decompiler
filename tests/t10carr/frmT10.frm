VERSION 5.00
Begin VB.Form frmT10 
   Caption         =   "T10"
   ClientHeight    =   3000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   4000
   LinkTopic       =   "Form1"
   ScaleHeight     =   3000
   ScaleWidth      =   4000
   StartUpPosition =   3  'Windows Default
   Begin VB.TextBox txt 
      Height          =   285
      Index           =   1
      Left            =   2000
      TabIndex        =   4
      Text            =   "second"
      Top             =   1200
      Width           =   1500
   End
   Begin VB.TextBox txt 
      Height          =   285
      Index           =   0
      Left            =   2000
      TabIndex        =   3
      Text            =   "first"
      Top             =   800
      Width           =   1500
   End
   Begin VB.CheckBox chk 
      Caption         =   "two"
      Height          =   255
      Index           =   2
      Left            =   120
      TabIndex        =   2
      Top             =   1200
      Width           =   1500
   End
   Begin VB.CheckBox chk 
      Caption         =   "one"
      Height          =   255
      Index           =   1
      Left            =   120
      TabIndex        =   1
      Top             =   800
      Width           =   1500
   End
   Begin VB.CheckBox chk 
      Caption         =   "zero"
      Height          =   255
      Index           =   0
      Left            =   120
      TabIndex        =   0
      Top             =   400
      Width           =   1500
   End
   Begin VB.Label lbl 
      Caption         =   "plain"
      Height          =   255
      Left            =   120
      TabIndex        =   5
      Top             =   2000
      Width           =   1500
   End
End
Attribute VB_Name = "frmT10"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

Private Sub Form_Load()
    Dim i As Integer, c As Control, n As Integer
    Open App.Path & "\out.txt" For Output As #1
    chk(1).Value = 1
    For i = 0 To 2
        Print #1, "chk"; i; chk(i).Value; chk(i).Caption
    Next
    Print #1, "count"; chk.Count; chk.LBound; chk.UBound
    Load chk(3)
    chk(3).Caption = "new"
    chk(3).Top = chk(2).Top + 400
    Print #1, chk(3).Caption; chk.Count; chk(3).Visible
    Unload chk(3)
    Print #1, "after unload"; chk.Count
    For Each c In txt
        Print #1, "txt"; c.Index; c.Text
    Next
    n = 2
    SetCap chk(n), "byref"
    Print #1, chk(2).Caption
    txt(n - 1).Text = txt(0).Text & "+"
    Print #1, txt(1).Text; Len(txt(1).Text)
    lbl.Caption = chk(0).Caption & "/" & txt(1).Text
    Print #1, lbl.Caption
    Print #1, "controls"; Me.Controls.Count
    Close #1
    Unload Me
End Sub

Private Sub SetCap(c As CheckBox, s As String)
    c.Caption = s
End Sub

Private Sub chk_Click(Index As Integer)
    Print #1, "click"; Index; chk(Index).Value
End Sub
