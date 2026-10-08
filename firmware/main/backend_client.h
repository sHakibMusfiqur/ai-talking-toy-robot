#pragma once

#include <stddef.h>

#include "esp_err.h"

esp_err_t backend_client_init(void);
esp_err_t backend_register(char *pairing_code_out, size_t pairing_code_len);
esp_err_t backend_refresh_token(void);
const char *backend_device_token(void);
int backend_token_expires_in(void);
