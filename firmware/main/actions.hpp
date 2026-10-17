#pragma once

#include <stddef.h>

#include "esp_err.h"

esp_err_t actions_init(void);
void actions_handle_command(const char *payload, size_t len);
