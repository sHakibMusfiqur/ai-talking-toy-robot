#include "telemetry.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "robot_mqtt.h"
#include "sdkconfig.h"
#include "wifi.h"

static const char *TAG = "telemetry";

#define STATE_MAX 32
#define STATUS_JSON_MAX 256
#define EVENT_JSON_MAX 512

static char s_state[STATE_MAX] = "BOOT";
static bool s_pending_mqtt_disconnected;
static bool s_started_flag;
static bool s_connected_flag;

void telemetry_set_state(const char *state)
{
    if (state == NULL) {
        return;
    }
    snprintf(s_state, sizeof(s_state), "%s", state);
}

const char *telemetry_state(void)
{
    return s_state;
}

static int publish_event(const char *event_type, const char *payload_obj)
{
    char json[EVENT_JSON_MAX];
    int written = snprintf(json, sizeof(json),
                           "{\"event_type\":\"%s\",\"payload\":%s}",
                           event_type, payload_obj != NULL ? payload_obj : "{}");
    if (written <= 0 || (size_t)written >= sizeof(json)) {
        return -1;
    }
    int rc = robot_mqtt_publish_event(json, (size_t)written);
    if (rc < 0) {
        ESP_LOGW(TAG, "event %s not delivered (transport down)", event_type);
    }
    return rc;
}

void telemetry_publish_status_now(void)
{
    char json[STATUS_JSON_MAX];
    int written;

    int rssi = wifi_rssi();
    if (rssi != INT32_MIN) {
        written = snprintf(json, sizeof(json),
                           "{\"online\":true,\"state\":\"%s\",\"rssi\":%d}",
                           s_state, rssi);
    } else {
        written = snprintf(json, sizeof(json),
                           "{\"online\":true,\"state\":\"%s\"}", s_state);
    }
    if (written <= 0 || (size_t)written >= sizeof(json)) {
        return;
    }
    int rc = robot_mqtt_publish_status(json, (size_t)written);
    if (rc < 0) {
        ESP_LOGW(TAG, "status not delivered (transport down)");
    }
}

void telemetry_publish_heartbeat_now(void)
{
    int rc = robot_mqtt_publish_heartbeat(TELEMETRY_HEARTBEAT_PAYLOAD,
                                          strlen(TELEMETRY_HEARTBEAT_PAYLOAD));
    if (rc < 0) {
        ESP_LOGW(TAG, "heartbeat not delivered (transport down)");
    }
}

void telemetry_publish_firmware_started(void)
{
    if (s_started_flag) {
        return;
    }
    s_started_flag = true;
    char payload[128];
    snprintf(payload, sizeof(payload), "{\"firmware\":\"%s\"}",
             esp_app_get_description()->version);
    publish_event("firmware.started", payload);
}

void telemetry_publish_mqtt_connected(void)
{
    if (s_pending_mqtt_disconnected) {
        s_pending_mqtt_disconnected = false;
        publish_event("mqtt.disconnected", "{\"reason\":\"connection_lost\"}");
    }
    s_connected_flag = true;
    telemetry_publish_status_now();
    publish_event("mqtt.connected", "{}");
}

void telemetry_publish_mqtt_disconnected(void)
{
    if (!s_connected_flag) {
        return;
    }
    s_connected_flag = false;
    s_pending_mqtt_disconnected = true;
}

static void status_task(void *arg)
{
    (void)arg;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(CONFIG_ROBOT_STATUS_INTERVAL_S * 1000));
        if (robot_mqtt_connected()) {
            telemetry_publish_status_now();
        }
    }
}

static void heartbeat_task(void *arg)
{
    (void)arg;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(CONFIG_ROBOT_HEARTBEAT_INTERVAL_S * 1000));
        if (robot_mqtt_connected()) {
            telemetry_publish_heartbeat_now();
        }
    }
}

esp_err_t telemetry_init(void)
{
    BaseType_t ok = xTaskCreate(status_task, "robot_status", 4096, NULL, 5,
                                NULL);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ok = xTaskCreate(heartbeat_task, "robot_heartbeat", 4096, NULL, 5, NULL);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
