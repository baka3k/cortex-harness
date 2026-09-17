Attribute VB_Name = "malformed"
' Intentionally malformed file: this must NOT sink the batch. The worker is
' expected to report ok=false (or a degraded payload) while every other file
' still parses.
Option Explicit

Public Sub Broken(
    Dim unclosed As Long
    If unclosed Then
End Sub

Public Function ) BadSyntax( As Long
    Broken = ???
