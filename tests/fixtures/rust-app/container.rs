use std::fmt::Display;

// Generic struct with type parameters
struct Container<T> {
    items: Vec<T>,
    capacity: usize,
}

impl<T> Container<T> {
    fn new(capacity: usize) -> Container<T> {
        Container {
            items: Vec::new(),
            capacity,
        }
    }

    fn push(&mut self, item: T) {
        self.items.push(item);
    }

    fn len(&self) -> usize {
        self.items.len()
    }
}

// Union (unsafe, but valid Rust syntax)
union IntOrFloat {
    i: i32,
    f: f32,
}

// Type alias with generics
type IntContainer = Container<i32>;

// Function signature (no body — declaration in a trait context would use this)
trait Processor {
    fn process(&self, data: &[u8]) -> Vec<u8>;
}

fn run_container() {
    let mut c: IntContainer = Container::new(10);
    c.push(42);
    c.push(99);
    let n = c.len();
}

fn macro_test() {
    let v = vec![1, 2, 3];
    let s = format!("count = {}", v.len());
    println!("{}", s);
}
