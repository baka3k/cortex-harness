// Cohort: virtual dispatch must stay conservative evidence.
struct Base {
    virtual int step(int v) = 0;
};
struct Derived : Base {
    int step(int v) override { return v + 1; }
};
int drive(Base& b) {
    return b.step(1);
}
