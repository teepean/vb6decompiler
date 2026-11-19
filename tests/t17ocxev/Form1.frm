VERSION 5.00
Object = "{831FDD16-0C5C-11D2-A9FC-0000F8754DA1}#2.0#0"; "MSCOMCTL.OCX"
Begin VB.Form Form1 
   Caption         =   "F"
   ClientHeight    =   3000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   5000
   ScaleHeight     =   3000
   ScaleWidth      =   5000
   Begin MSComctlLib.ListView lv 
      Height          =   1500
      Left            =   100
      TabIndex        =   0
      Top             =   100
      Width           =   3000
      _ExtentX        =   5292
      _ExtentY        =   2646
      View            =   3
      LabelEdit       =   1
      _Version        =   393217
      Appearance      =   1
      NumItems        =   0
   End
   Begin VB.TextBox txt 
      Height          =   285
      Left            =   100
      TabIndex        =   1
      Text            =   "t"
      Top             =   1800
      Width           =   1500
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Private hits As Long

Private Sub Form_Load()
    Open App.Path & "\out.txt" For Output As #1
    lv.ColumnHeaders.Add , , "A", 1000
    lv.ListItems.Add , , "one"
    Print #1, "items"; lv.ListItems.Count; hits
    Close #1
    Unload Me
End Sub

Private Sub lv_ItemClick(ByVal Item As MSComctlLib.ListItem)
    hits = hits + 1
End Sub

Private Sub lv_ColumnClick(ByVal ColumnHeader As MSComctlLib.ColumnHeader)
    hits = hits + 2
End Sub

Private Sub lv_KeyDown(KeyCode As Integer, Shift As Integer)
    hits = hits + 3
End Sub

Private Sub lv_BeforeLabelEdit(Cancel As Integer)
    Cancel = 1
End Sub

Private Sub lv_MouseDown(Button As Integer, Shift As Integer, x As Single, y As Single)
    hits = hits + 4
End Sub

Private Sub txt_KeyPress(KeyAscii As Integer)
    hits = hits + 5
End Sub
