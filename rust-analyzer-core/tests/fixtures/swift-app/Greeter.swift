// swift-app/Greeter.swift
// Tests: class + property + init + method + call + import

import Foundation

class Greeter {
    var name: String

    init(name: String) {
        self.name = name
    }

    func hello(who: String) -> String {
        return formatGreeting(self.name, who)
    }
}

func formatGreeting(_ name: String, _ who: String) -> String {
    return "Hello, \(name) -> \(who)"
}