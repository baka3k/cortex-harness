VERSION 1.0 CLASS
BEGIN
  MultiUse = -1
END
Attribute VB_Name = "clsEvents"
' Class fixture: user-defined event declaration (plan 260917-1628 plane M1).
Option Explicit

' Raised before a save is committed
Public Event BeforeSave(Cancel As Boolean)

Public Sub Save()
    Dim cancelSave As Boolean
    RaiseEvent BeforeSave(cancelSave)
End Sub
