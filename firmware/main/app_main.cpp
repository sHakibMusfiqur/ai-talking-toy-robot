#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "actions.hpp"
#include "backend_client.hpp"
#include "conversation.hpp"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "identity.hpp"
#include "nvs_flash.h"
#include "robot_mqtt.hpp"
#include "sdkconfig.h"
#include "telemetry.hpp"
#include "wifi.hpp"
#include "wifi_provision.hpp"

static const char *TAG = "app_main";

constexpr int BACKOFF_MIN_MS = 1000;
constexpr int BACKOFF_MAX_MS = 30000;
constexpr int CONNECT_WAIT_MS = 15000;
constexpr int WIFI_WAIT_MS = 30000;
constexpr int TOKEN_REFRESH_MIN_S = 60;
#define AUTH_FAILURE_PROVISION_THRESHOLD 3

typedef enum {
    CONN_WIFI_DISCONNECTED,
    CONN_WIFI_CONNECTED,
    CONN_MQTT_DISCONNECTED,
    CONN_MQTT_CONNECTED,
} conn_state_t;

static const char *conn_state_name(conn_state_t state)
{
    switch (state) {
    case CONN_WIFI_DISCONNECTED:
        return "WIFI_DISCONNECTED";
    case CONN_WIFI_CONNECTED:
        return "WIFI_CONNECTED";
    case CONN_MQTT_DISCONNECTED:
        return "MQTT_DISCONNECTED";
    case CONN_MQTT_CONNECTED:
        return "MQTT_CONNECTED";
    }
    return "UNKNOWN";
}

static uint32_t backoff_next(uint32_t current_ms)
{
    uint32_t next = current_ms * 2;
    return next > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : next;
}

static void ensure_registered(void)
{
    uint32_t backoff_ms = BACKOFF_MIN_MS;
    while (!identity_is_registered()) {
        if (!wifi_wait_connected(WIFI_WAIT_MS)) {
            ESP_LOGW(TAG, "state=%s, registration deferred",
                     conn_state_name(CONN_WIFI_DISCONNECTED));
            continue;
        }
        char pairing_code[16] = {0};
        esp_err_t err = backend_register(pairing_code, sizeof(pairing_code));
        if (err == ESP_OK) {
            if (pairing_code[0] != '\0') {
                printf(
                    "\n==================================================\n"
                    " Robot registered. Pairing code (single use):\n"
                    "   %s\n"
                    " Enter it in the web dashboard to link this robot.\n"
                    "==================================================\n\n",
                    pairing_code);
            }
            return;
        }
        ESP_LOGW(TAG, "registration retry in %" PRIu32 " ms", backoff_ms);
        vTaskDelay(pdMS_TO_TICKS(backoff_ms));
        backoff_ms = backoff_next(backoff_ms);
    }
}

static void ensure_token(void)
{
    uint32_t backoff_ms = BACKOFF_MIN_MS;
    while (strlen(backend_device_token()) == 0) {
        if (backend_refresh_token() == ESP_OK) {
            return;
        }
        ESP_LOGW(TAG, "auth exchange retry in %" PRIu32 " ms", backoff_ms);
        vTaskDelay(pdMS_TO_TICKS(backoff_ms));
        backoff_ms = backoff_next(backoff_ms);
    }
}

static int64_t token_refresh_due_us(int expires_in)
{
    int effective = expires_in - 300;
    if (effective < TOKEN_REFRESH_MIN_S) {
        effective = TOKEN_REFRESH_MIN_S;
    }
    return (int64_t)effective * 1000000;
}

static void check_factory_reset(void)
{
    if (CONFIG_ROBOT_FACTORY_RESET_GPIO < 0) {
        return;
    }
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_ROBOT_FACTORY_RESET_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&io) != ESP_OK) {
        return;
    }
    if (gpio_get_level(
            static_cast<gpio_num_t>(CONFIG_ROBOT_FACTORY_RESET_GPIO)) == 0) {
        ESP_LOGW(TAG,
                 "factory-reset button held at boot, erasing saved Wi-Fi "
                 "(GPIO %d)",
                 CONFIG_ROBOT_FACTORY_RESET_GPIO);
        wifi_erase_credentials();
    }
}

extern "C" void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES ||
        err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS init failed (%s), halted", esp_err_to_name(err));
        return;
    }

    if (identity_init() != ESP_OK) {
        ESP_LOGE(TAG, "device identity unavailable, halted");
        return;
    }
    ESP_LOGI(TAG, "device id: %s", identity_device_id());

    if (telemetry_init() != ESP_OK || actions_init() != ESP_OK ||
        robot_mqtt_init() != ESP_OK) {
        ESP_LOGE(TAG, "subsystem init failed, halted");
        return;
    }

    check_factory_reset();

    if (!wifi_has_saved_credentials()) {
        provision_run("no_credentials", false);
        return;
    }

    err = wifi_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi unavailable (%s), halted", esp_err_to_name(err));
        return;
    }

    if (!wifi_wait_connected(CONFIG_ROBOT_WIFI_SETUP_FALLBACK_MS) &&
        wifi_auth_failure_count() >= AUTH_FAILURE_PROVISION_THRESHOLD) {
        provision_run("auth_failure", true);
        return;
    }

    telemetry_set_state("BOOT");

    ensure_registered();
    ensure_token();

    if (conversation_init() != ESP_OK) {
        ESP_LOGW(TAG, "conversation capture unavailable");
    }

    uint32_t backoff_ms = BACKOFF_MIN_MS;
    int64_t token_refreshed_at = esp_timer_get_time();

    for (;;) {
        conn_state_t state = CONN_WIFI_DISCONNECTED;
        if (!wifi_wait_connected(WIFI_WAIT_MS)) {
            ESP_LOGW(TAG, "state=%s", conn_state_name(state));
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            backoff_ms = backoff_next(backoff_ms);
            continue;
        }
        state = CONN_WIFI_CONNECTED;
        ESP_LOGI(TAG, "state=%s", conn_state_name(state));

        if (strlen(backend_device_token()) == 0) {
            ensure_token();
        }

        err = robot_mqtt_start();
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "state=%s (%s), retry in %" PRIu32 " ms",
                     conn_state_name(CONN_MQTT_DISCONNECTED),
                     esp_err_to_name(err), backoff_ms);
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            backoff_ms = backoff_next(backoff_ms);
            continue;
        }

        if (!robot_mqtt_wait_connected(CONNECT_WAIT_MS)) {
            ESP_LOGW(TAG, "state=%s (connect timeout), retry in %" PRIu32 " ms",
                     conn_state_name(CONN_MQTT_DISCONNECTED), backoff_ms);
            robot_mqtt_stop();
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            backoff_ms = backoff_next(backoff_ms);
            continue;
        }

        backoff_ms = BACKOFF_MIN_MS;
        state = CONN_MQTT_CONNECTED;
        ESP_LOGI(TAG, "state=%s", conn_state_name(state));
        telemetry_publish_firmware_started();
        telemetry_publish_mqtt_connected();

        while (robot_mqtt_connected()) {
            if (!wifi_is_connected()) {
                ESP_LOGW(TAG, "state=%s (Wi-Fi link lost)",
                         conn_state_name(CONN_WIFI_DISCONNECTED));
                break;
            }
            int64_t now = esp_timer_get_time();
            int64_t due =
                token_refresh_due_us(backend_token_expires_in());
            if (now - token_refreshed_at >= due) {
                if (backend_refresh_token() == ESP_OK) {
                    token_refreshed_at = now;
                } else {
                    ESP_LOGW(TAG, "auth refresh failed, retrying shortly");
                    token_refreshed_at = now - due + 60 * 1000000;
                }
            }
            vTaskDelay(pdMS_TO_TICKS(1000));
        }

        robot_mqtt_stop();
        state = CONN_MQTT_DISCONNECTED;
        telemetry_publish_mqtt_disconnected();
        ESP_LOGW(TAG, "state=%s, reconnect in %" PRIu32 " ms",
                 conn_state_name(state), backoff_ms);
        vTaskDelay(pdMS_TO_TICKS(backoff_ms));
        backoff_ms = backoff_next(backoff_ms);
    }
}
