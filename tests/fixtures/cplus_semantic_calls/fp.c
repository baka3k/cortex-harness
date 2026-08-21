/* Cohort: indirect calls through function pointers and callbacks. */
static int add_one(int v) { return v + 1; }
static int add_two(int v) { return v + 2; }
int apply(int (*op)(int), int v) {
    return op(v);
}
int run(int mode) {
    int (*fn)(int) = mode ? add_one : add_two;
    return apply(fn, mode);
}
