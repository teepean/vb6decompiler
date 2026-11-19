Attribute VB_Name = "mM"
Option Explicit

' Fast code around files and Variants: what a save and load of records needs to be in step.
Public Type TGene
    tipo As Integer
    value As Integer
End Type

Public Type TShot
    exist As Boolean
    x As Single
    y As Single
    owner As Long
    age As Integer
    who As String
    dna() As TGene
    dnalen As Integer
    gen As Integer
    stored As Boolean
End Type

Public Type TSpec
    nam As String
    cant As Boolean
    weak As Boolean
End Type

Public Type TOpts
    count As Integer
    moved As Boolean
    spec(5) As TSpec
End Type

Public Shots() As TShot
Public MaxShot As Long
Public Opts As TOpts
Public Names(10) As String
Public Moves As Integer
Private OutF As Integer

Private Function FileContinue(n As Integer) As Boolean
    Dim fe As Byte
    Dim p As Long
    Dim k As Integer
    p = Seek(n)
    Do
        If Not EOF(n) Then
            Get #n, , fe
        Else
            fe = 254
        End If
        k = k + 1
        If fe <> 254 Then
            FileContinue = True
            Exit Do
        End If
    Loop While k < 3
    Get #n, p - 1, fe
End Function

Private Sub SaveShot(n As Integer, t As Long)
    Dim k As Integer
    Dim fe As Byte
    With Shots(t)
        Put #n, , .exist
        Put #n, , .x
        Put #n, , .y
        Put #n, , .owner
        Put #n, , .age
        k = Len(.who)
        Put #n, , k
        Put #n, , .who
        k = .dnalen
        Put #n, , k
        For k = 1 To .dnalen
            Put #n, , .dna(k).tipo
            Put #n, , .dna(k).value
        Next k
        Put #n, , .gen
        Put #n, , .stored
    End With
    fe = 254
    Put #n, , fe
    Put #n, , fe
    Put #n, , fe
End Sub

Private Sub LoadShot(n As Integer, t As Long)
    Dim k As Integer
    Dim i As Integer
    Dim fe As Byte
    With Shots(t)
        Get #n, , .exist
        Get #n, , .x
        Get #n, , .y
        Get #n, , .owner
        Get #n, , .age
        Get #n, , k: .who = Space(k)
        Get #n, , .who
        Get #n, , k
        If k > 0 Then
            ReDim .dna(k)
            For i = 1 To k
                Get #n, , .dna(i).tipo
                Get #n, , .dna(i).value
            Next i
        End If
        .dnalen = k
        Get #n, , .gen
        Get #n, , .stored
        While FileContinue(n)
            Get #n, , fe
        Wend
        Get #n, , fe
        Get #n, , fe
        Get #n, , fe
    End With
End Sub

Private Sub SaveAll(p As String)
    Dim t As Long
    Dim k As Integer
    Open p For Binary As #1
    Put #1, , Opts.count
    For k = 0 To Opts.count - 1
        Put #1, , Opts.spec(k).cant
    Next k
    Put #1, , Opts.moved
    Put #1, , MaxShot
    For t = 1 To MaxShot
        SaveShot 1, t
    Next t
    Close #1
End Sub

Private Sub LoadAll(p As String)
    Dim t As Long
    Dim k As Integer
    Open p For Binary As #1
    Get #1, , Opts.count
    For k = 0 To Opts.count - 1
        Opts.spec(k).cant = False
        If Not EOF(1) Then Get #1, , Opts.spec(k).cant
    Next k
    Opts.moved = False
    If Not EOF(1) Then Get #1, , Opts.moved
    MaxShot = 0
    If Not EOF(1) Then Get #1, , MaxShot
    If MaxShot <> 0 And MaxShot > 0 And MaxShot < 1000000 Then
        ReDim Shots(MaxShot)
        For t = 1 To MaxShot
            LoadShot 1, t
        Next t
    End If
    Close #1
End Sub

Private Function Position(ByRef key As String, ByRef Keys) As Integer
    Dim k As Integer
    Dim u As Integer
    u = UBound(Keys)
    k = 1
    While Keys(k) <> key And Keys(k) <> "" And k < u
        k = k + 1
    Wend
    Position = 0
    If Keys(k) = key Or k < u Then
        Position = k
        Keys(k) = key
    End If
End Function

Private Sub Counted(ByVal a As Integer, ByVal b As Integer)
    Static olda As Integer
    Static oldb As Integer
    Static seen As Boolean
    If a > 0 And Not seen Then
        If b > 0 Then
            olda = a
            oldb = b
            seen = True
        End If
    End If
    If a > 10 Then
        If (a - olda) > (b - oldb) Then Print #OutF, "ahead"; a - olda; b - oldb
    End If
    Print #OutF, olda; oldb; seen
End Sub

Private Sub Bump()
    If Moves < 4 Then Moves = Moves + 1
End Sub

Sub Main()
    Dim t As Long
    Dim n As Integer
    OutF = 9
    Open "out.txt" For Output As #OutF
    Opts.count = 3
    Opts.spec(1).cant = True
    Opts.moved = True
    MaxShot = 3
    ReDim Shots(MaxShot)
    For t = 1 To MaxShot
        With Shots(t)
            .exist = t <> 2
            .x = t * 1.5
            .y = t / 2
            .owner = t * 100
            .age = t + 1
            .who = "w" & t
            .dnalen = t
            ReDim .dna(t)
            For n = 1 To t
                .dna(n).tipo = n
                .dna(n).value = n * t
            Next n
            .gen = t * 3
            .stored = t = 3
        End With
    Next t
    SaveAll "t92.bin"
    Opts.count = 3
    Opts.spec(1).cant = False
    Opts.moved = False
    MaxShot = 0
    Erase Shots
    LoadAll "t92.bin"
    Print #OutF, Opts.count; Opts.spec(0).cant; Opts.spec(1).cant; Opts.spec(2).cant; Opts.moved; MaxShot
    For t = 1 To MaxShot
        With Shots(t)
            Print #OutF, .exist; .x; .y; .owner; .age; .who; .dnalen; .gen; .stored; .dna(.dnalen).value
        End With
    Next t
    Print #OutF, Position("a", Names); Position("b", Names); Position("a", Names); Names(1); Names(2)
    Counted 5, 0
    Counted 5, 7
    Counted 20, 8
    For n = 1 To 6
        Bump
    Next n
    Print #OutF, Moves
    Close #OutF
    Kill "t92.bin"
End Sub
