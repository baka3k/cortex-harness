Attribute VB_Name = "latebas"
' Late-bound fixture: receiver declared As Object cannot be resolved
' statically; the call must survive as POSSIBLE_CALLS with
' resolution_status=late_bound.
Option Explicit

Public Sub RunLate()
    Dim x As Object
    Set x = CreateObject("Sample.Thing")
    x.LateBound 5
End Sub
