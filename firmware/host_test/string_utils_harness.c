#include <stddef.h>
#include <stdio.h>

#include "conv_string.h"

typedef struct {
    const char *name;
    const char *input;
    int expected;
} case_t;

int main(void)
{
    static const case_t cases[] = {
        {"null", NULL, 0},
        {"empty", "", 0},
        {"single_space", " ", 0},
        {"tab", "\t", 0},
        {"newline", "\n", 0},
        {"carriage_return", "\r", 0},
        {"vertical_tab", "\v", 0},
        {"form_feed", "\f", 0},
        {"cr_lf", "\r\n", 0},
        {"mixed_whitespace", " \t\n \r ", 0},
        {"single_char", "a", 1},
        {"padded_char", " a ", 1},
        {"hello_world", "hello world", 1},
        {"zero_digit", "0", 1},
        {"tab_then_zero", "\t0", 1},
        {"zero_then_space", "0 ", 1},
        {"embedded_space", "hello world", 1},
    };
    const size_t total = sizeof(cases) / sizeof(cases[0]);
    int failures = 0;
    for (size_t i = 0; i < total; i++) {
        int got = conv_string_has_nonwhitespace(cases[i].input) ? 1 : 0;
        if (got != cases[i].expected) {
            printf("FAIL %s: got %d want %d\n", cases[i].name, got,
                   cases[i].expected);
            failures++;
        }
    }
    printf("TOTAL %zu FAILURES %d\n", total, failures);
    return failures == 0 ? 0 : 1;
}
