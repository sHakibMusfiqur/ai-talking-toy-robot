#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t wifi_init_stack(void);
esp_err_t wifi_start(void);
bool wifi_has_saved_credentials(void);
bool wifi_get_saved_ssid(char *out, size_t len);
bool wifi_save_credentials(const char *ssid, const char *pass);
void wifi_erase_credentials(void);
esp_err_t wifi_sta_connect_with(const char *ssid, const char *pass);
void wifi_set_autoreconnect(bool enable);
uint32_t wifi_auth_failure_count(void);
bool wifi_is_connected(void);
bool wifi_wait_connected(uint32_t timeout_ms);
int wifi_rssi(void);
