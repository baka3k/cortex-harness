// swift-app/Drawable.swift
// Tests: protocol + protocol_function_declaration + inheritance (EXTENDS) + concrete impl

protocol Drawable {
    func draw()
    var color: String { get set }
}

protocol Resizable: Drawable {
    func resize(to size: Int)
}

class Square: Resizable {
    var color: String = "red"
    var size: Int = 0

    func draw() {
        render()
    }

    func resize(to size: Int) {
        self.size = size
    }
}

class Container {
    var items: [Int] = []

    subscript(idx: Int) -> Int {
        return items[idx]
    }

    deinit {
        cleanup()
    }
}

typealias MyInt = Int