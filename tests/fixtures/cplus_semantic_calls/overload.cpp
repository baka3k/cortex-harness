// Cohort: overload resolution ambiguity.
static int pick(int v) { return v; }
static int pick(double v) { return static_cast<int>(v); }
int run(int value) {
    return pick(value) + pick(2.5);
}
