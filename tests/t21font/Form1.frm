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
   Begin VB.ListBox List1 
      Height          =   600
      Left            =   2400
      TabIndex        =   1
      Top             =   600
      Width           =   1500
   End
   Begin MSComctlLib.ListView lv 
      Height          =   1000
      Left            =   100
      TabIndex        =   0
      Top             =   600
      Width           =   2000
      _ExtentX        =   3528
      _ExtentY        =   1764
      LabelWrap       =   -1  'True
      HideSelection   =   -1  'True
      _Version        =   393217
      ForeColor       =   -2147483640
      BackColor       =   -2147483643
      BorderStyle     =   1
      Appearance      =   1
      NumItems        =   0
   End
End
Attribute VB_Name = "Form1"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Private Sub Form_Load()
    Dim k As Integer, f As StdFont, v As Variant
    Open App.Path & "\out.txt" For Output As #1
    For k = 8 To 11
        If k = 10 Then
            List1.Font.Size = 10
        ElseIf k = 9 Then
            List1.Font.Size = 9
        End If
        Print #1, List1.FontSize;
    Next k
    Print #1,
    ' (lv.Font.Bold = True: Wine's VB6 says "Variable uses an Automation type
    ' not supported"; the VM's builds it - a Variant cast to IFontDisp)
    Set v = List1.Font
    Set f = v
    f.Bold = True
    Print #1, List1.FontBold; f.Bold
    f.Bold = False
    Print #1, List1.FontBold; f.Bold
    Print #1, CStr("x" & "        " & nyt())
    Print #1, CStr(polku("a", "b") & "|")
    Close #1
    Unload Me
End Sub
