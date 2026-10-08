#include "wifi.h"

#include <inttypes.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "sdkconfig.h"

static const char *TAG = "wifi";

#define WIFI_CONNECTED_BIT BIT0
#define BACKOFF_MIN_MS 1000
#define BACKOFF_MAX_MS 30000

static EventGroupHandle_t s_events;
static esp_timer_handle_t s_reconnect_timer;
static uint32_t s_backoff_ms = BACKOFF_MIN_MS;
static bool s_started;

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

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data)
{
    (void)arg;
    (void)event_data;
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_events, WIFI_CONNECTED_BIT);
        schedule_reconnect();
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        esp_timer_stop(s_reconnect_timer);
        s_backoff_ms = BACKOFF_MIN_MS;
        xEventGroupSetBits(s_events, WIFI_CONNECTED_BIT);
        ESP_LOGI(TAG, "station connected");
    }
}

esp_err_t wifi_start(void)
{
    if (s_started) {
        return ESP_OK;
    }
    if (strlen(CONFIG_ROBOT_WIFI_SSID) == 0) {
        ESP_LOGE(TAG, "Wi-Fi SSID not configured (set it in menuconfig)");
        return ESP_ERR_INVALID_STATE;
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

    wifi_config_t wifi_cfg = {0};
    strlcpy((char *)wifi_cfg.sta.ssid, CONFIG_ROBOT_WIFI_SSID,
            sizeof(wifi_cfg.sta.ssid));
    strlcpy((char *)wifi_cfg.sta.password, CONFIG_ROBOT_WIFI_PASSWORD,
            sizeof(wifi_cfg.sta.password));
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    s_started = true;
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
