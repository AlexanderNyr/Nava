#include <cstdint>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>

static inline int64_t add(int64_t a, int64_t b) {
    int64_t result;
    if (__builtin_add_overflow(a, b, &result)) std::abort();
    return result;
}
static inline int64_t mul(int64_t a, int64_t b) {
    int64_t result;
    if (__builtin_mul_overflow(a, b, &result)) std::abort();
    return result;
}
static inline int64_t rem(int64_t a, int64_t b) {
    if (b == 0 || (a == INT64_MIN && b == -1)) std::abort();
    return a % b;
}
int main() {
    int64_t n;
    if (std::scanf("%" SCNd64, &n) != 1) return 1;
    int64_t i = 0, x = 1;
    while (i < n) {
        x = rem(add(mul(x, 1664525), 1013904223), 1000000007);
        i = add(i, 1);
    }
    std::printf("%" PRId64 "\n", x);
}
