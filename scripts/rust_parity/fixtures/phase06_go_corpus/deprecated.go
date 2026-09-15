package svc9

// Legacy$i exists to be deleted between gate corpus versions.
func LegacyOne() int { return 1 }

// LegacyTwo exists to be deleted between gate corpus versions.
func LegacyTwo() int { return 2 }

// DeprecatedType is removed in corpus v2.
type DeprecatedType struct{ Value int }
