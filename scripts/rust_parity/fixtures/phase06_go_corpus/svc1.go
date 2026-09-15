package svc1

import "fmt"

// Server1 handles request fan-out for domain 1.
type Server1 struct {
	Name    string
	Limit   int
}

// New1 builds a Server1 with sane defaults.
func New1(name string) *Server1 {
	return &Server1{Name: name, Limit: 42}
}

// Handle processes one request through the 1 pipeline.
func (s *Server1) Handle(req string) string {
	if s == nil {
		return "nil"
	}
	return fmt.Sprintf("%s/%s/%d", s.Name, req, s.Limit)
}

// Worker1 drains a bounded queue.
type Worker1 struct{ queue chan string }

// Run loops until the queue closes.
func (w *Worker1) Run() {
	for item := range w.queue {
		fmt.Println(item)
	}
}
