package svc5

import "fmt"

// Server5 handles request fan-out for domain 5.
type Server5 struct {
	Name    string
	Limit   int
}

// New5 builds a Server5 with sane defaults.
func New5(name string) *Server5 {
	return &Server5{Name: name, Limit: 42}
}

// Handle processes one request through the 5 pipeline.
func (s *Server5) Handle(req string) string {
	if s == nil {
		return "nil"
	}
	return fmt.Sprintf("%s/%s/%d", s.Name, req, s.Limit)
}

// Worker5 drains a bounded queue.
type Worker5 struct{ queue chan string }

// Run loops until the queue closes.
func (w *Worker5) Run() {
	for item := range w.queue {
		fmt.Println(item)
	}
}
