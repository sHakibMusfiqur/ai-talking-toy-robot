#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t wifi_start(void);
bool wifi_is_connected(void);
bool wifi_wait_connected(uint32_t timeout_ms);
int wifi_rssi(void);
