#include <stddef.h>
#include <stdio.h>

#include "conv_retry_after.h"

typedef struct {
    const char *name;
    const char *input;
    int expected;
} case_t;

int main(void)
{
    static const case_t cases[] = {
        {"null", NULL, -1},
        {"empty", "", -1},
        {"zero", "0", 0},
        {"ordinary_30", "30", 30},
        {"ordinary_1", "1", 1},
        {"leading_zeros", "000120", 120},
        {"below_cap", "8640", 8640},
        {"max_accepted", "86400", 86400},
        {"above_max", "86401", -1},
        {"above_cap_small", "99999", -1},
        {"very_long_digits", "99999999999999999999999999", -1},
        {"long_midrange_digits", "00000000000000000000120", 120},
        {"negative", "-5", -1},
        {"plus_sign", "+5", -1},
        {"leading_space", " 120", -1},
        {"trailing_space", "120 ", -1},
        {"inner_space", "1 20", -1},
        {"alpha", "abc", -1},
        {"mixed_alnum", "12a", -1},
        {"http_date", "Wed, 21 Oct 2026 07:28:00 GMT", -1},
        {"overflow_edge_86401_digits", "0000000000000000086401", -1},
    };
    const size_t total = sizeof(cases) / sizeof(cases[0]);
    int failures = 0;
    for (size_t i = 0; i < total; i++) {
        int got = conv_parse_retry_after(cases[i].input);
        if (got != cases[i].expected) {
            printf("FAIL %s: got %d want %d\n", cases[i].name, got,
                   cases[i].expected);
            failures++;
        }
    }
    printf("TOTAL %zu FAILURES %d\n", total, failures);
    return failures == 0 ? 0 : 1;
}
