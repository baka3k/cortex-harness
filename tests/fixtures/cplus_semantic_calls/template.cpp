// Cohort: dependent template calls stay candidates until instantiation.
template <typename T>
struct Box {
    T value;
    template <typename U>
    T combine(U other) { return value + other; }
};
int use_box(int v) {
    Box<int> box{v};
    return box.combine(2);
}
