Attribute VB_Name = "mM"
Option Explicit

Sub Main()
    Open App.Path & "\out.txt" For Output As #1
    Form1.OnOtherForm
    Form1.OnList
    Form1.OnVariantObject
    Form1.OnError
    Unload Form1
    Close #1
End Sub
