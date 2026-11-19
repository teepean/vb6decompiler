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
   Begin MSComctlLib.ProgressBar pb1 
      Height          =   300
      Left            =   100
      TabIndex        =   0
      Top             =   200
      Width           =   1500
      _ExtentX        =   2646
      _ExtentY        =   529
      _Version        =   393216
      Appearance      =   1
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Private Sub Form_Load()
    Dim x As Variant, y As Single
    Open App.Path & "\out.txt" For Output As #1
    pb1.Max = 200
    pb1.Value = 30
    x = pb1.Max
    y = pb1.Value
    pb1.Refresh
    pb1.Visible = False
    Print #1, x; y; pb1.Min; pb1.Visible
    Close #1
    Unload Me
End Sub
