Attribute VB_Name = "modApi"
' Plane-hydration fixture (plan 260917-1628): enum + constants + Declare +
' optional/ParamArray arity + dictionary (default-member) call.
Option Explicit

' Color codes for the fixture UI
Public Enum AppColor
    acBackground = 1
    acHighlight = 2
End Enum

' Retry budget for the fixture sync loop
Public Const MAX_RETRY = 3
Public Const APP_TITLE As String = "Fixture"

' kernel32 uptime probe used by ElapsedMs
Private Declare Function GetTickCount Lib "kernel32" Alias "GetTickCount" () As Long

' Reads the process uptime in milliseconds.
' Wraps the kernel32 GetTickCount declare above.
' Exercises a multi-line block comment directly above a function.
Public Function ElapsedMs() As Long
    ElapsedMs = GetTickCount()
End Function

Public Sub Flexible(ByVal a As Long, Optional ByVal b As Long = 0, ParamArray rest())
    Debug.Print a, b
End Sub

Public Sub CallFlexible()
    ' arity range: callee accepts 1..n args (optional + ParamArray)
    Flexible 1
    Flexible 1, 2, 3, 4
End Sub

' Dictionary (default-member) access: must survive as POSSIBLE_CALLS
Public Sub ReadField()
    Dim rs As Object
    Set rs = CreateObject("ADODB.Recordset")
    Dim v As Variant
    v = rs!FieldName
End Sub
