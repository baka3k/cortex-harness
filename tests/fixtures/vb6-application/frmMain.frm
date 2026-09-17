VERSION 5.00
Begin VB.Form frmMain
   Caption         =   "Main"
   ClientHeight    =   6345
   ClientLeft      =   60
   ClientTop       =   345
   ClientWidth     =   7020
   LinkTopic       =   "Form1"
   ScaleHeight     =   6345
   ScaleWidth      =   7020
   StartUpPosition =   3
   BeginProperty Font
      Name            =   "MS Sans Serif"
      Size            =   8.25
      Charset         =   0
      Weight          =   400
      Underline       =   0
      Italic          =   0
      Strikethrough   =   0
   EndProperty
   Begin VB.Frame fraData
      Caption         =   "Data"
      Height          =   2415
      Left            =   120
      TabIndex        =   3
      Top             =   1320
      Width           =   6735
      Begin VB.TextBox txtEmail
         Height          =   285
         Left            =   1320
         TabIndex        =   5
         Text            =   ""
         Top             =   1320
         Width           =   5175
      End
      Begin VB.TextBox txtName
         Height          =   285
         Left            =   1320
         TabIndex        =   4
         Text            =   "nested"
         Top             =   600
         Width           =   5175
      End
      Begin VB.Label lblEmail
         Caption         =   "Email"
         Height          =   255
         Left            =   240
         TabIndex        =   7
         Top             =   1320
         Width           =   975
      End
      Begin VB.Label lblName
         Caption         =   "Name"
         Height          =   255
         Left            =   240
         TabIndex        =   6
         Top             =   600
         Width           =   975
      End
   End
   Begin VB.ListBox lstItems
      Height          =   1425
      ItemData        =   "frmMain.frx":0000
      Left            =   120
      List            =   "frmMain.frx":0002
      TabIndex        =   2
      Tab(1).Control(0)=   "txtName"
      Tab(1).Control(1)=   "txtEmail"
      Tab(1).ControlCount=   2
      Top             =   4560
      Width           =   6735
   End
   Begin VB.CommandButton cmdGo
      Caption         =   "Go"
      Height          =   495
      Left            =   120
      TabIndex        =   0
      Top             =   240
      Width           =   1215
   End
   Begin VB.Label lblInfo
      Caption         =   "Fixture form"
      Height          =   255
      Left            =   1560
      TabIndex        =   1
      Top             =   360
      Width           =   5295
   End
End
Attribute VB_Name = "frmMain"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
' Form fixture: the designer block above Attribute VB_Name is kept by the
' adapter (keep-designer default) and feeds the controls[] plane; line
' numbers in the payload refer to the ORIGINAL file positions. Form_Load
' exercises cross-module + Me member + no-paren calls; the With block
' exercises member resolution against a typed variable.
Option Explicit

Private Sub Form_Load()
    ' cross-module qualified call into a standard module
    modMain.DoWork
    ' Me member call (form intrinsic method)
    Me.Refresh
    ' no-paren call with args
    DoSomething 7, 8
    ' With block member call on typed variable
    Dim ord As clsOrder
    Set ord = New clsOrder
    With ord
        .ProcessOrder 1
    End With
    ' property access via typed variable (Property Let + Get)
    ord.Total = 5
    Dim amount As Double
    amount = ord.Total
    ' interface dispatch: static callee set is ambiguous across implementers
    Dim ship As IShip
    Set ship = New clsShip
    ship.Ship_Order 9
    ' late-bound call through form default instance of the other form
    frmAbout.Show
End Sub

Public Sub TestSameName()
    ' same-name procedure as frmAbout.TestSameName -> ambiguous unqualified
    Debug.Print "frmMain.TestSameName"
End Sub

' Reloads fixture data when Go is clicked
Private Sub cmdGo_Click()
    Form_Load
End Sub

' Wired handler for the nested txtName TextBox
Private Sub txtName_Change()
    ' wired to the nested txtName TextBox via VB6_EVENT_SUFFIXES matching
End Sub

Private Sub Helper_Click_Validate()
    ' anti-false-positive: suffix is NOT an event name, must stay unwired
End Sub
