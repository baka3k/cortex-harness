VERSION 1.0 CLASS
BEGIN
  MultiUse = -1
END
Attribute VB_Name = "clsSink"
' Anchor fixture (plan 260924): WithEvents variable + handler, class lifecycle
' (Class_Initialize/Terminate), Static local, ReDim Preserve, COM-typed
' receiver, dictionary (bang) access.
Option Explicit

Private WithEvents mSource As clsSource

Public userStatus As Integer

Private Sub Class_Initialize()
    ' `As New` instantiation anchor + lifecycle anchor
    Set mSource = New clsSource
End Sub

Private Sub Class_Terminate()
    Set mSource = Nothing
End Sub

Private Sub mSource_Progress(ByVal percent As Long)
    ' WithEvents handler: wired to the mSource variable anchor
    Debug.Print percent
End Sub

Public Sub ResizeBuffer(ByVal size As Long)
    Static buffer() As String
    ReDim Preserve buffer(size)
    buffer(0) = "x"
End Sub

Public Sub QueryRows()
    ' COM-typed receiver anchor (ADODB.Recordset) + bang access
    Dim rs As ADODB.Recordset
    Set rs = New ADODB.Recordset
    rs.Open "SELECT 1"
    rs!FieldName = 1
    rs.Close
End Sub
