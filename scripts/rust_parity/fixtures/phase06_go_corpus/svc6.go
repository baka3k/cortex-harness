package svc6

import "fmt"

// Server6 handles request fan-out for domain 6.
type Server6 struct {
	Name    string
	Limit   int
}

// New6 builds a Server6 with sane defaults.
func New6(name string) *Server6 {
	return &Server6{Name: name, Limit: 42}
}

// Handle processes one request through the 6 pipeline.
func (s *Server6) Handle(req string) string {
	if s == nil {
		return "nil"
	}
	return fmt.Sprintf("%s/%s/%d", s.Name, req, s.Limit)
}

// Worker6 drains a bounded queue.
type Worker6 struct{ queue chan string }

// Run loops until the queue closes.
func (w *Worker6) Run() {
	for item := range w.queue {
		fmt.Println(item)
	}
}
