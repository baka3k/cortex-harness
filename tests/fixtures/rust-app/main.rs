use std::fmt;
use std::collections::HashMap;

extern crate serde;

/// A simple struct with fields.
struct Greeter {
    name: String,
    count: u32,
}

impl Greeter {
    fn new(name: &str) -> Greeter {
        Greeter {
            name: name.to_string(),
            count: 0,
        }
    }

    fn hello(&self, who: &str) -> String {
        format!("Hello, {} from {}", who, self.name)
    }

    fn increment(&mut self) {
        self.count += 1;
    }
}

enum Status {
    Active,
    Inactive,
    Pending(u32),
}

trait Drawable {
    fn draw(&self);
    fn area(&self) -> u32;
}

type Score = u64;

fn main() {
    let mut g = Greeter::new("world");
    let msg = g.hello("user");
    g.increment();
    let s = Status::Active;
    println!("{}", msg);
}

fn helper(a: i32, b: i32) -> i32 {
    a + b
}
