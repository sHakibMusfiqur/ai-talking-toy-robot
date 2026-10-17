#include "wifi.hpp"

#include <inttypes.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "nvs.h"
#include "sdkconfig.h"

static const char *TAG = "wifi";

constexpr EventBits_t WIFI_CONNECTED_BIT = BIT0;
constexpr int BACKOFF_MIN_MS = 1000;
constexpr int BACKOFF_MAX_MS = 30000;

#define NVS_NAMESPACE "wifi"
#define NVS_KEY_SSID "ssid"
#define NVS_KEY_PASS "pass"

static EventGroupHandle_t s_events;
static esp_timer_handle_t s_reconnect_timer;
static uint32_t s_backoff_ms = BACKOFF_MIN_MS;
static bool s_started;
static bool s_stack_inited;
static bool s_autoreconnect = true;
static volatile uint32_t s_auth_fail_count;

static void reconnect_timer_cb(void *arg)
{
    (void)arg;
    esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "reconnect request failed (%s)", esp_err_to_name(err));
    }
}

static void schedule_reconnect(void)
{
    esp_timer_stop(s_reconnect_timer);
    esp_err_t err =
        esp_timer_start_once(s_reconnect_timer, (uint64_t)s_backoff_ms * 1000);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "reconnect timer error (%s)", esp_err_to_name(err));
    }
    ESP_LOGI(TAG, "retrying Wi-Fi in %" PRIu32 " ms", s_backoff_ms);
    s_backoff_ms *= 2;
    if (s_backoff_ms > BACKOFF_MAX_MS) {
        s_backoff_ms = BACKOFF_MAX_MS;
    }
}

static bool is_auth_failure_reason(uint8_t reason)
{
    switch (reason) {
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_MIC_FAILURE:
        return true;
    default:
        return false;
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data)
{
    (void)arg;
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        if (s_autoreconnect) {
            esp_wifi_connect();
        }
    } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_events, WIFI_CONNECTED_BIT);
        if (event_data != NULL) {
            const wifi_event_sta_disconnected_t *disc =
                (const wifi_event_sta_disconnected_t *)event_data;
            if (is_auth_failure_reason(disc->reason)) {
                s_auth_fail_count++;
                ESP_LOGW(TAG, "Wi-Fi association rejected (reason %d)",
                         (int)disc->reason);
            }
        }
        if (s_autoreconnect) {
            schedule_reconnect();
        }
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        esp_timer_stop(s_reconnect_timer);
        s_backoff_ms = BACKOFF_MIN_MS;
        s_auth_fail_count = 0;
        xEventGroupSetBits(s_events, WIFI_CONNECTED_BIT);
        ESP_LOGI(TAG, "station connected");
    }
}

static esp_err_t nvs_read_str(const char *key, char *out, size_t out_len)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return err;
    }
    size_t len = out_len;
    err = nvs_get_str(handle, key, out, &len);
    nvs_close(handle);
    return err;
}

bool wifi_has_saved_credentials(void)
{
    char ssid[33] = {0};
    if (nvs_read_str(NVS_KEY_SSID, ssid, sizeof(ssid)) == ESP_OK &&
        ssid[0] != '\0') {
        return true;
    }
    return strlen(CONFIG_ROBOT_WIFI_SSID) > 0;
}

bool wifi_get_saved_ssid(char *out, size_t len)
{
    if (out == NULL || len == 0) {
        return false;
    }
    out[0] = '\0';
    if (nvs_read_str(NVS_KEY_SSID, out, len) == ESP_OK && out[0] != '\0') {
        return true;
    }
    if (CONFIG_ROBOT_WIFI_SSID[0] != '\0' &&
        strlen(CONFIG_ROBOT_WIFI_SSID) < len) {
        strlcpy(out, CONFIG_ROBOT_WIFI_SSID, len);
        return true;
    }
    return false;
}

bool wifi_save_credentials(const char *ssid, const char *pass)
{
    if (ssid == NULL || ssid[0] == '\0') {
        return false;
    }
    if (pass == NULL) {
        pass = "";
    }
    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) {
        ESP_LOGE(TAG, "cannot open Wi-Fi settings store");
        return false;
    }
    esp_err_t err = nvs_set_str(handle, NVS_KEY_SSID, ssid);
    if (err == ESP_OK) {
        err = nvs_set_str(handle, NVS_KEY_PASS, pass);
    }
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "failed to store Wi-Fi settings (%s)",
                 esp_err_to_name(err));
        return false;
    }
    ESP_LOGI(TAG, "Wi-Fi settings saved (key value not logged)");
    return true;
}

void wifi_erase_credentials(void)
{
    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
        (void)nvs_erase_key(handle, NVS_KEY_SSID);
        (void)nvs_erase_key(handle, NVS_KEY_PASS);
        (void)nvs_commit(handle);
        nvs_close(handle);
    }
    ESP_LOGI(TAG, "saved Wi-Fi settings erased");
}

esp_err_t wifi_sta_connect_with(const char *ssid, const char *pass)
{
    if (ssid == NULL || ssid[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    wifi_config_t wifi_cfg = {0};
    strlcpy((char *)wifi_cfg.sta.ssid, ssid, sizeof(wifi_cfg.sta.ssid));
    strlcpy((char *)wifi_cfg.sta.password, (pass != NULL) ? pass : "",
            sizeof(wifi_cfg.sta.password));
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
    if (err != ESP_OK) {
        return err;
    }
    return esp_wifi_connect();
}

void wifi_set_autoreconnect(bool enable)
{
    s_autoreconnect = enable;
    if (!enable && s_reconnect_timer != NULL) {
        esp_timer_stop(s_reconnect_timer);
    }
}

uint32_t wifi_auth_failure_count(void)
{
    return s_auth_fail_count;
}

esp_err_t wifi_init_stack(void)
{
    if (s_stack_inited) {
        return ESP_OK;
    }

    s_events = xEventGroupCreate();
    if (s_events == NULL) {
        return ESP_ERR_NO_MEM;
    }

    const esp_timer_create_args_t timer_args = {
        .callback = reconnect_timer_cb,
        .name = "wifi_retry",
    };
    esp_err_t err = esp_timer_create(&timer_args, &s_reconnect_timer);
    if (err != ESP_OK) {
        return err;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    s_stack_inited = true;
    return ESP_OK;
}

esp_err_t wifi_start(void)
{
    if (s_started) {
        return ESP_OK;
    }

    esp_err_t err = wifi_init_stack();
    if (err != ESP_OK) {
        return err;
    }

    char ssid[33] = {0};
    char pass[65] = {0};
    bool from_nvs = nvs_read_str(NVS_KEY_SSID, ssid, sizeof(ssid)) == ESP_OK &&
                    ssid[0] != '\0';
    if (from_nvs) {
        (void)nvs_read_str(NVS_KEY_PASS, pass, sizeof(pass));
    } else {
        strlcpy(ssid, CONFIG_ROBOT_WIFI_SSID, sizeof(ssid));
        strlcpy(pass, CONFIG_ROBOT_WIFI_PASSWORD, sizeof(pass));
    }
    if (strlen(ssid) == 0) {
        memset(pass, 0, sizeof(pass));
        ESP_LOGE(TAG, "no Wi-Fi credentials available");
        return ESP_ERR_INVALID_STATE;
    }

    wifi_config_t wifi_cfg = {0};
    strlcpy((char *)wifi_cfg.sta.ssid, ssid, sizeof(wifi_cfg.sta.ssid));
    strlcpy((char *)wifi_cfg.sta.password, pass, sizeof(wifi_cfg.sta.password));
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    memset(pass, 0, sizeof(pass));

    s_autoreconnect = true;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    s_started = true;
    ESP_LOGI(TAG, "station starting (credentials %s)",
             from_nvs ? "from device settings" : "from build config");
    return ESP_OK;
}

bool wifi_is_connected(void)
{
    if (s_events == NULL) {
        return false;
    }
    return (xEventGroupGetBits(s_events) & WIFI_CONNECTED_BIT) != 0;
}

bool wifi_wait_connected(uint32_t timeout_ms)
{
    if (s_events == NULL) {
        return false;
    }
    TickType_t ticks = (timeout_ms == 0) ? 0 : pdMS_TO_TICKS(timeout_ms);
    EventBits_t bits = xEventGroupWaitBits(s_events, WIFI_CONNECTED_BIT, pdFALSE,
                                           pdTRUE, ticks);
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

int wifi_rssi(void)
{
    wifi_ap_record_t ap_info;
    if (!wifi_is_connected()) {
        return INT32_MIN;
    }
    if (esp_wifi_sta_get_ap_info(&ap_info) != ESP_OK) {
        return INT32_MIN;
    }
    return ap_info.rssi;
}
