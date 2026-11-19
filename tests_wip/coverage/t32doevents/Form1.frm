VERSION 5.00
Begin VB.Form Form1 
   Caption         =   "F"
   ClientHeight    =   3000
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   4000
   ScaleHeight     =   3000
   ScaleWidth      =   4000
   Begin VB.Timer Tm 
      Enabled         =   0   'False
      Interval        =   1
      Left            =   100
      Top             =   100
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit
Private cnt As Long

Private Sub Tm_Timer()
    cnt = cnt + 1
    If cnt >= 3 Then Tm.Enabled = False
End Sub

Private Sub Form_Load()
    Dim n As Integer, v As Variant, l As Long
    Open App.Path & "\out.txt" For Output As #1
    ' (Me.Show: see t60meshow)
    Me.Visible = True
    n = DoEvents
    Print #1, n
    v = DoEvents()
    Print #1, v; TypeName(v)
    l = DoEvents + 10
    Print #1, l
    If DoEvents() > 0 Then Print #1, "forms"
    Print #1, DoEvents()
    Print #1, Pump(3)
    Tm.Enabled = True
    Do While cnt < 3
        DoEvents
    Loop
    Print #1, cnt
    Close #1
    Unload Me
End Sub
