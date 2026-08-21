/* Cohort: plain direct calls (C).
 * target() is a same-file direct call; missing() has no definition anywhere.
 */
void target(void) {}
void caller(void) {
    target();
    missing();
}
