#include <cstdint>
#include <cinttypes>
#include <cstdio>

int main() {
    int64_t n;
    if (std::scanf("%" SCNd64, &n) != 1) return 1;
    int64_t i = 0, x = 1;
    while (i < n) {
        x = (x * 1664525 + 1013904223) % 1000000007;
        ++i;
    }
    std::printf("%" PRId64 "\n", x);
}
