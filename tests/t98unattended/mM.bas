Attribute VB_Name = "mM"
Option Explicit

' Unattended and Retained are the header's thread flags, and App tells.  Small code, no version block to speak of.
Sub Main()
    Dim i As Long, s As Long
    Open App.Path & "\out.txt" For Output As #1
    Print #1, App.UnattendedApp, App.RetainedProject
    Print #1, App.Major; App.Minor; App.Revision
    Print #1, "company [" & App.CompanyName & "]"
    For i = 1 To 10
        s = s + i * i
    Next
    Print #1, s
    Close #1
End Sub
