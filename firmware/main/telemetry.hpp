#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define TELEMETRY_HEARTBEAT_PAYLOAD "{}"

esp_err_t telemetry_init(void);
void telemetry_set_state(const char *state);
const char *telemetry_state(void);
void telemetry_publish_status_now(void);
void telemetry_publish_heartbeat_now(void);
void telemetry_publish_firmware_started(void);
void telemetry_publish_mqtt_connected(void);
void telemetry_publish_mqtt_disconnected(void);
void telemetry_publish_audio_capture_started(void);
void telemetry_publish_audio_capture_completed(uint32_t pcm_bytes,
                                               uint32_t duration_ms);
void telemetry_publish_audio_capture_failed(const char *reason);
void telemetry_publish_audio_upload_succeeded(int attempts, bool replayed);
void telemetry_publish_audio_upload_failed(const char *reason);
void telemetry_publish_audio_unavailable(const char *reason);
