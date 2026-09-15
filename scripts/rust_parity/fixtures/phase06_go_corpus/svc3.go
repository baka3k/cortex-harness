package svc3

import "fmt"

// Server3 handles request fan-out for domain 3.
type Server3 struct {
	Name    string
	Limit   int
}

// New3 builds a Server3 with sane defaults.
func New3(name string) *Server3 {
	return &Server3{Name: name, Limit: 42}
}

// Handle processes one request through the 3 pipeline.
func (s *Server3) Handle(req string) string {
	if s == nil {
		return "nil"
	}
	return fmt.Sprintf("%s/%s/%d", s.Name, req, s.Limit)
}

// Worker3 drains a bounded queue.
type Worker3 struct{ queue chan string }

// Run loops until the queue closes.
func (w *Worker3) Run() {
	for item := range w.queue {
		fmt.Println(item)
	}
}
