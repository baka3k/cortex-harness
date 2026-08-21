/* Cohort: macro-wrapped calls and internal linkage. */
#define WRAP(x) helper(x)
static int helper(int v) { return v; }
int other_file_helper(int v); /* possibly another TU */
static int internal(int v) { return WRAP(v); }
int entry(int v) {
    return internal(v) + other_file_helper(v);
}
