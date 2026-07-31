// Simple C++ class with methods, base classes, and namespaces.
// Used as a fixture for Rust vs Python differential testing.

#ifndef SIMPLE_CLASS_HPP
#define SIMPLE_CLASS_HPP

namespace demo {

class Shape {
public:
    Shape() = default;
    virtual ~Shape() = default;

    virtual double area() const = 0;
};

class Circle : public Shape {
public:
    explicit Circle(double r) : radius_(r) {}
    double area() const override { return 3.14159 * radius_ * radius_; }

private:
    double radius_;
};

double compute_area(const Shape& s) {
    return s.area();
}

}  // namespace demo

#endif  // SIMPLE_CLASS_HPP
