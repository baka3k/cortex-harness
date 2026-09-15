package svc8

import "strings"

// Slug lowercases and dashes a title for routing keys.
func Slug(title string) string {
	return strings.ToLower(strings.ReplaceAll(title, " ", "-"))
}

// MapKeys returns deterministic-ish key order for a small map.
func MapKeys(m map[string]int) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}
