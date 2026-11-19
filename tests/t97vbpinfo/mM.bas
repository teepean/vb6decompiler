Attribute VB_Name = "mM"
Option Explicit

' What a project's properties leave in the EXE and App gives back: the version block and the header's strings.
' (The EXE's own icon and file name are compared by tools/roundtrip.py, which goes by the EXE.)
Sub Report()
    Open App.Path & "\out.txt" For Output As #1
    Print #1, App.Major; App.Minor; App.Revision
    Print #1, "title [" & App.Title & "]"
    Print #1, "name [" & App.EXEName & "]"
    Print #1, "comments [" & App.Comments & "]"
    Print #1, "company [" & App.CompanyName & "]"
    Print #1, "description [" & App.FileDescription & "]"
    Print #1, "copyright [" & App.LegalCopyright & "]"
    Print #1, "trademarks [" & App.LegalTrademarks & "]"
    Print #1, "product [" & App.ProductName & "]"
    Print #1, "help [" & Mid$(App.HelpFile, InStrRev(App.HelpFile, "\") + 1) & "]"
    Print #1, Form2.Twice(21)
    Unload Form2
    Close #1
End Sub
