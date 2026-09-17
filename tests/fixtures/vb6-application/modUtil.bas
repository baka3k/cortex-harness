Attribute VB_Name = "modUtil"
' Cross-module callee fixtures: Public Function CalcTotal (unqualified target),
' Public Sub LogMessage (line-continuation target), Private HelperSub (must stay
' unresolved when called outside this module).
Option Explicit

Public Function CalcTotal(ByVal a As Double, ByVal b As Double) As Double
    CalcTotal = a + b
End Function

Public Sub LogMessage(ByVal msg As String)
    Debug.Print msg
End Sub

Private Sub HelperSub()
    ' module-private: callable only within modUtil
    Debug.Print "helper"
End Sub

Public Sub RunHelper()
    ' module-local private call (resolved within modUtil)
    HelperSub
End Sub
