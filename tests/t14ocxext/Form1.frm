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
    Dim v As Variant, o As Object
    Open App.Path & "\out.txt" For Output As #1
    v = pb1.Name: Print #1, "Name"; v
    v = pb1.Left: Print #1, "Left"; v
    v = pb1.Top: Print #1, "Top"; v
    v = pb1.Width: Print #1, "Width"; v
    v = pb1.Height: Print #1, "Height"; v
    v = pb1.Visible: Print #1, "Visible"; v
    v = pb1.Tag: Print #1, "Tag"; v
    v = pb1.TabIndex: Print #1, "TabIndex"; v
    v = pb1.WhatsThisHelpID: Print #1, "WhatsThisHelpID"; v
    v = pb1.ToolTipText: Print #1, "ToolTipText"; v
    v = pb1.DragMode: Print #1, "DragMode"; v
    v = pb1.Align: Print #1, "Align"; v
    Set o = pb1.Parent
    Set o = pb1.Container
    Set o = pb1.Object
    Set o = pb1.DragIcon
    pb1.ZOrder 0
    pb1.Move 10, 10
    Close #1
    Unload Me
End Sub
