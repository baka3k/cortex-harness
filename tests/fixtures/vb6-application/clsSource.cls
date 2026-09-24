VERSION 1.0 CLASS
BEGIN
  MultiUse = -1
END
Attribute VB_Name = "clsSource"
' Anchor fixture (plan 260924): event source for the WithEvents pair.
Option Explicit

Public Event Progress(ByVal percent As Long)

Public Sub Run()
    RaiseEvent Progress(50)
End Sub
