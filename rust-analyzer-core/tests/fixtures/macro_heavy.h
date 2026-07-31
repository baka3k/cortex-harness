// Header file with macros, defines, and forward declarations.
#ifndef MACRO_HEAVY_H
#define MACRO_HEAVY_H

#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define LIKELY(x) __builtin_expect(!!(x), 1)

#ifndef API_EXPORT
#define API_EXPORT __attribute__((visibility("default")))
#endif

API_EXPORT int process(int value);
API_EXPORT void cleanup(void);

class MacroContainer {
public:
    MacroContainer(int size);
    ~MacroContainer();

    int max(int a, int b) { return MAX(a, b); }
    int min(int a, int b) { return MIN(a, b); }

private:
    int size_;
};

#endif  // MACRO_HEAVY_H
