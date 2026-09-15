package svc2

import "fmt"

// Server2 handles request fan-out for domain 2.
type Server2 struct {
	Name    string
	Limit   int
}

// New2 builds a Server2 with sane defaults.
func New2(name string) *Server2 {
	return &Server2{Name: name, Limit: 42}
}

// Handle processes one request through the 2 pipeline.
func (s *Server2) Handle(req string) string {
	if s == nil {
		return "nil"
	}
	return fmt.Sprintf("%s/%s/%d", s.Name, req, s.Limit)
}

// Worker2 drains a bounded queue.
type Worker2 struct{ queue chan string }

// Run loops until the queue closes.
func (w *Worker2) Run() {
	for item := range w.queue {
		fmt.Println(item)
	}
}
