#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t robot_mqtt_init(void);
esp_err_t robot_mqtt_start(void);
void robot_mqtt_stop(void);
bool robot_mqtt_connected(void);
bool robot_mqtt_wait_connected(uint32_t timeout_ms);
int robot_mqtt_publish_status(const char *payload, size_t len);
int robot_mqtt_publish_heartbeat(const char *payload, size_t len);
int robot_mqtt_publish_event(const char *payload, size_t len);
