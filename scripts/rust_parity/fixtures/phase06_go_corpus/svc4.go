package svc4

import "fmt"

// Server4 handles request fan-out for domain 4.
type Server4 struct {
	Name    string
	Limit   int
}

// New4 builds a Server4 with sane defaults.
func New4(name string) *Server4 {
	return &Server4{Name: name, Limit: 42}
}

// Handle processes one request through the 4 pipeline.
func (s *Server4) Handle(req string) string {
	if s == nil {
		return "nil"
	}
	return fmt.Sprintf("%s/%s/%d", s.Name, req, s.Limit)
}

// Worker4 drains a bounded queue.
type Worker4 struct{ queue chan string }

// Run loops until the queue closes.
func (w *Worker4) Run() {
	for item := range w.queue {
		fmt.Println(item)
	}
}
