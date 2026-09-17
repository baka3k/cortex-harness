Attribute VB_Name = "modMain"
' Case matrix fixture: no-paren sub call, Call statement, MsgBox builtin,
' line continuation, cross-module unqualified + qualified calls, ambiguous
' unqualified call, string-literal trap.
Option Explicit

Public Sub DoWork()
    ' case: no-paren call with args (module-local sub)
    DoSomething 1, 2
    ' case: no-paren call without args
    InitData
    ' case: Call statement
    Call InitData
    ' case: builtin external (MsgBox) - no resolution target
    MsgBox "x"
    ' case: line continuation (LogMessage _
    '       "done")
    LogMessage _
        "done"
    ' case: cross-module unqualified public call
    CalcTotal 1, 2
    ' case: qualified cross-module call
    Dim total As Double
    total = modUtil.CalcTotal(3, 4)
    ' case: ambiguous unqualified (TestSameName exists in frmMain AND frmAbout)
    TestSameName
    ' case: string-literal trap - must NOT create a call edge
    Dim caption As String
    caption = "Call Fake(x)"
    Debug.Print caption
End Sub

Public Sub DoSomething(ByVal a As Long, ByVal b As Long)
    Dim s As Long
    s = a + b
End Sub

Public Sub InitData()
    ' no-op initializer
End Sub

Public Function Main() As Integer
    DoWork
    Main = 0
End Function

Public Sub UseGlobalForm()
    ' predeclared-id default instances: cross-module member calls on forms
    frmMain.TestSameName
    frmAbout.ShowAbout
    ' form intrinsic stays external
    frmAbout.Hide
End Sub
