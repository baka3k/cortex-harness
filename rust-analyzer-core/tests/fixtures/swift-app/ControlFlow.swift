// swift-app/ControlFlow.swift
// Tests: branches (if, guard, switch, do/catch) + loops + nested calls

import Foundation

class Worker {
    func safeUnwrap(_ x: Int?) -> Int {
        guard let v = x else {
            log("missing value")
            return 0
        }
        log("got value")
        return process(v)
    }

    func process(_ n: Int) -> Int {
        var total = 0
        for i in 0..<n {
            if i % 2 == 0 {
                total += i
            } else {
                total -= 1
            }
        }
        return total
    }

    func pick(value: Int) -> String {
        switch value {
        case 0:
            return "zero"
        case 1...9:
            return "single"
        default:
            return "many"
        }
    }

    func log(_ s: String) {
        // intentionally empty
    }
}