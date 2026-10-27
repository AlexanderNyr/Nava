#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

int main(void) {
    int64_t a, b;
    if (scanf("%" SCNd64 " %" SCNd64, &a, &b) != 2) return 1;
    while (b != 0) {
        const int64_t r = a % b;
        a = b;
        b = r;
    }
    printf("%" PRId64 "\n", a);
    return 0;
}
