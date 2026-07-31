package main

import (
	"fmt"
	"strings"
)

// Greeter holds a name to greet.
type Greeter struct {
	Name string
	Age  int
}

// Hello returns a greeting for the given name.
func (g Greeter) Hello(who string) string {
	return fmt.Sprintf("Hello %s, I am %s", who, g.Name)
}

// Shout is an unexported helper.
func shout(msg string) string {
	return strings.ToUpper(msg)
}

func main() {
	g := Greeter{Name: "world", Age: 30}
	greeting := g.Hello(g.Name)
	for i := 0; i < 3; i++ {
		fmt.Println(shout(greeting))
	}
	if g.Age > 18 {
		fmt.Println("adult")
	} else {
		fmt.Println("minor")
	}
}
