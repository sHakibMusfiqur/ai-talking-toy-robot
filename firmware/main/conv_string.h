#pragma once

#include <stdbool.h>

static inline bool conv_string_has_nonwhitespace(const char *s)
{
    if (s == NULL) {
        return false;
    }
    for (const char *p = s; *p != '\0'; p++) {
        if (*p != ' ' && *p != '\t' && *p != '\n' && *p != '\v' &&
            *p != '\f' && *p != '\r') {
            return true;
        }
    }
    return false;
}
