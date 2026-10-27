#include <cstdint>
#include <iostream>

int main() {
    std::int64_t a, b;
    if (!(std::cin >> a >> b)) return 1;
    while (b != 0) {
        const std::int64_t r = a % b;
        a = b;
        b = r;
    }
    std::cout << a << '\n';
}
