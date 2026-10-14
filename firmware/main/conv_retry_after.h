#pragma once

#define CONV_RETRY_AFTER_MAX_S 86400
#define CONV_RETRY_AFTER_CAP_S 30
#define CONV_RETRY_AFTER_DEFAULT_S 2

static inline int conv_parse_retry_after(const char *value)
{
    if (value == NULL || value[0] == '\0') {
        return -1;
    }
    long seconds = 0;
    for (const char *p = value; *p != '\0'; p++) {
        if (*p < '0' || *p > '9') {
            return -1;
        }
        int digit = *p - '0';
        if (seconds > (CONV_RETRY_AFTER_MAX_S - digit) / 10) {
            return -1;
        }
        seconds = seconds * 10 + digit;
    }
    return (int)seconds;
}

static inline int conv_retry_after_wait_seconds(int retry_after_s)
{
    if (retry_after_s < 0 || retry_after_s > CONV_RETRY_AFTER_CAP_S) {
        return CONV_RETRY_AFTER_DEFAULT_S;
    }
    return retry_after_s;
}
