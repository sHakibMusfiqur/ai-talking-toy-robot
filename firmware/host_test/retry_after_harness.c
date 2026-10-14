#include <limits.h>
#include <stddef.h>
#include <stdio.h>

#include "conv_retry_after.h"

typedef struct {
    const char *name;
    const char *input;
    int expected;
} case_t;

typedef struct {
    const char *name;
    int retry_after_s;
    int expected_wait;
} clamp_case_t;

typedef struct {
    const char *name;
    const char *header;
    int expected_wait;
} pipeline_case_t;

static int parse_clamp(const char *header)
{
    return conv_retry_after_wait_seconds(conv_parse_retry_after(header));
}

int main(void)
{
    static const case_t parse_cases[] = {
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
        {"overflow_edge_86401_digits", "00000000000000000086401", -1},
    };
    static const clamp_case_t clamp_cases[] = {
        {"invalid_minus_one", -1, 2},
        {"zero_stays_zero", 0, 0},
        {"one_stays_one", 1, 1},
        {"max_allowed_30", 30, 30},
        {"over_cap_31", 31, 2},
        {"much_larger_valid_int", 86400, 2},
        {"int_max", INT_MAX, 2},
    };
    static const pipeline_case_t pipeline_cases[] = {
        {"null_header_uses_default", NULL, 2},
        {"empty_header_uses_default", "", 2},
        {"zero_header_is_zero", "0", 0},
        {"thirty_header_is_thirty", "30", 30},
        {"thirty_one_uses_default", "31", 2},
        {"one_twenty_over_cap_uses_default", "120", 2},
        {"leading_zeros_fifteen", "000015", 15},
        {"malformed_uses_default", "abc", 2},
        {"negative_uses_default", "-5", 2},
        {"http_date_uses_default", "Wed, 21 Oct 2026 07:28:00 GMT", 2},
    };
    int failures = 0;
    const size_t parse_total = sizeof(parse_cases) / sizeof(parse_cases[0]);
    for (size_t i = 0; i < parse_total; i++) {
        int got = conv_parse_retry_after(parse_cases[i].input);
        if (got != parse_cases[i].expected) {
            printf("FAIL parse %s: got %d want %d\n", parse_cases[i].name, got,
                   parse_cases[i].expected);
            failures++;
        }
    }
    printf("PARSE TOTAL %zu FAILURES %d\n", parse_total, failures);

    int clamp_failures = 0;
    const size_t clamp_total = sizeof(clamp_cases) / sizeof(clamp_cases[0]);
    for (size_t i = 0; i < clamp_total; i++) {
        int got = conv_retry_after_wait_seconds(clamp_cases[i].retry_after_s);
        if (got != clamp_cases[i].expected_wait) {
            printf("FAIL clamp %s: got %d want %d\n", clamp_cases[i].name, got,
                   clamp_cases[i].expected_wait);
            clamp_failures++;
        }
    }
    printf("CLAMP TOTAL %zu FAILURES %d\n", clamp_total, clamp_failures);
    failures += clamp_failures;

    int pipeline_failures = 0;
    const size_t pipeline_total =
        sizeof(pipeline_cases) / sizeof(pipeline_cases[0]);
    for (size_t i = 0; i < pipeline_total; i++) {
        int got = parse_clamp(pipeline_cases[i].header);
        if (got != pipeline_cases[i].expected_wait) {
            printf("FAIL pipeline %s: got %d want %d\n",
                   pipeline_cases[i].name, got,
                   pipeline_cases[i].expected_wait);
            pipeline_failures++;
        }
    }
    printf("PIPELINE TOTAL %zu FAILURES %d\n", pipeline_total,
           pipeline_failures);
    failures += pipeline_failures;

    return failures == 0 ? 0 : 1;
}
