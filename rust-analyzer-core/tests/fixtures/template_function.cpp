// Template function with overload + specialization.
#include <vector>

template <typename T>
T identity(T value) {
    return value;
}

template <typename T>
T sum(const std::vector<T>& values) {
    T total = T{};
    for (const auto& v : values) {
        total = total + v;
    }
    return total;
}

template <>
int identity<int>(int value) {
    return value * 2;
}

int main() {
    auto x = identity(42);
    auto y = sum(std::vector<int>{1, 2, 3});
    return x + y;
}
