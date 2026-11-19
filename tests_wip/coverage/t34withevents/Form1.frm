VERSION 5.00
Begin VB.Form Form1 
   Caption         =   "F"
   ClientHeight    =   3000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   4000
   ScaleHeight     =   3000
   ScaleWidth      =   4000
   Begin VB.TextBox Text1 
      Height          =   300
      Left            =   100
      TabIndex        =   1
      Top             =   600
      Width           =   1500
   End
   Begin VB.CommandButton Command1 
      Caption         =   "B"
      Height          =   300
      Left            =   100
      TabIndex        =   0
      Top             =   100
      Width           =   1500
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

Private WithEvents src As cSrc
Attribute src.VB_VarHelpID = -1
Private WithEvents btn As VB.CommandButton
Attribute btn.VB_VarHelpID = -1
Private WithEvents txt As VB.TextBox
Attribute txt.VB_VarHelpID = -1
Private WithEvents frm As VB.Form
Attribute frm.VB_VarHelpID = -1

Private Sub src_Tick(ByVal n As Long)
    Print #1, "form tick"; n
End Sub

Private Sub src_Ask(q As String, Cancel As Boolean)
    Print #1, "ask "; q
    If q = "no" Then Cancel = True
    q = q & "2"
End Sub

Private Sub btn_Click()
    Print #1, "btn click "; btn.Caption
End Sub

Private Sub txt_Change()
    Print #1, "txt change "; txt.Text
End Sub

Private Sub frm_Resize()
    Print #1, "frm resize"
End Sub

Private Sub Form_Load()
    Dim a As cSrc, b As cSrc, l1 As cLis, l2 As cLis
    Open App.Path & "\out.txt" For Output As #1
    Set a = New cSrc
    Set b = New cSrc
    Set src = a
    a.Fire 1
    b.Fire 99
    Set src = b
    a.Fire 98
    b.Fire 2
    Print #1, b.Query("yes")
    Print #1, b.Query("no")
    Set src = Nothing
    b.Fire 97
    Set l1 = New cLis
    Set l2 = New cLis
    l1.Attach a
    l2.Attach a
    a.Fire 2
    Print #1, l1.Log; " "; l2.Log
    Print #1, a.Fan(); l1.Log
    l2.Attach b
    a.Fire 5
    Print #1, l1.Log; " "; l2.Log
    Set btn = Command1
    btn.Value = True
    Command1.Value = True
    Set btn = Nothing
    Command1.Value = True
    Set txt = Text1
    Text1.Text = "hello"
    txt.Text = "again"
    Set frm = Me
    Me.Width = Me.Width + 300
    Set frm = Nothing
    Close #1
    Unload Me
End Sub
