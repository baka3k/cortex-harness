package repo

import (
	"context"
	"io"
)

// Reader is a small read interface.
type Reader interface {
	Read(p []byte) (n int, err error)
	Close() error
}

// Record is a database row.
type Record struct {
	ID    int
	Value string
}

// MyReader adapts io.Reader to Reader.
type MyReader = io.Reader

// Store manages records.
type Store[T any] struct {
	items []T
}

// Get returns the first item.
func (s Store[T]) Get() T {
	if len(s.items) == 0 {
		var zero T
		return zero
	}
	return s.items[0]
}

// Add appends an item.
func (s *Store[T]) Add(item T) {
	s.items = append(s.items, item)
}

// Process reads from r and returns bytes read.
func Process(ctx context.Context, r Reader) (int, error) {
	buf := make([]byte, 1024)
	total := 0
	for {
		n, err := r.Read(buf)
		total += n
		if err != nil {
			break
		}
	}
	return total, nil
}
