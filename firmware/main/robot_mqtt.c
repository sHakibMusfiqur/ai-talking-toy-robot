#include "robot_mqtt.h"

#include <stdio.h>
#include <string.h>

#include "actions.h"
#include "backend_client.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "identity.h"
#include "mqtt_client.h"
#include "sdkconfig.h"
#include "wifi.h"

static const char *TAG = "robot_mqtt";

#define MQTT_CONNECTED_BIT BIT0
#define TOPIC_MAX 96
#define INBOUND_PAYLOAD_MAX 1024
#define LWT_PAYLOAD "{\"online\":false}"

typedef struct {
    char status[TOPIC_MAX];
    char heartbeat[TOPIC_MAX];
    char event[TOPIC_MAX];
    char command[TOPIC_MAX];
    char config_notify[TOPIC_MAX];
    char ota_notify[TOPIC_MAX];
    char content_notify[TOPIC_MAX];
} robot_topics_t;

static esp_mqtt_client_handle_t s_client;
static EventGroupHandle_t s_events;
static volatile bool s_connected;
static bool s_started;

static robot_topics_t s_topics;

static void build_topics(robot_topics_t *t)
{
    const char *id = identity_device_id();
    snprintf(t->status, sizeof(t->status), "robots/%s/status", id);
    snprintf(t->heartbeat, sizeof(t->heartbeat), "robots/%s/heartbeat", id);
    snprintf(t->event, sizeof(t->event), "robots/%s/event", id);
    snprintf(t->command, sizeof(t->command), "robots/%s/command", id);
    snprintf(t->config_notify, sizeof(t->config_notify),
             "robots/%s/config/notify", id);
    snprintf(t->ota_notify, sizeof(t->ota_notify), "robots/%s/ota/notify", id);
    snprintf(t->content_notify, sizeof(t->content_notify),
             "robots/%s/content/notify", id);
}

static bool topic_is(const char *topic, int topic_len, const char *expected)
{
    return topic_len == (int)strlen(expected) &&
           strncmp(topic, expected, (size_t)topic_len) == 0;
}

static void handle_inbound(const char *topic, int topic_len, const char *data,
                           int data_len)
{
    if (topic == NULL || data == NULL || data_len < 0) {
        return;
    }
    if (data_len >= INBOUND_PAYLOAD_MAX) {
        ESP_LOGW(TAG, "inbound payload too large, dropped");
        return;
    }

    char payload[INBOUND_PAYLOAD_MAX];
    memcpy(payload, data, (size_t)data_len);
    payload[data_len] = '\0';

    if (topic_is(topic, topic_len, s_topics.command)) {
        actions_handle_command(payload, (size_t)data_len);
        return;
    }

    if (topic_is(topic, topic_len, s_topics.config_notify) ||
        topic_is(topic, topic_len, s_topics.ota_notify) ||
        topic_is(topic, topic_len, s_topics.content_notify)) {
        cJSON *parsed = cJSON_Parse(payload);
        if (parsed != NULL) {
            const cJSON *version = cJSON_GetObjectItem(parsed, "version");
            if (cJSON_IsNumber(version)) {
                ESP_LOGI(TAG, "notify received (version %d)",
                         version->valueint);
            }
            cJSON_Delete(parsed);
        }
    }
}

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data)
{
    (void)arg;
    (void)base;
    esp_mqtt_event_handle_t event = event_data;

    switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "connected to broker");
        esp_mqtt_client_subscribe(s_client, s_topics.command, 1);
        esp_mqtt_client_subscribe(s_client, s_topics.config_notify, 1);
        esp_mqtt_client_subscribe(s_client, s_topics.ota_notify, 1);
        esp_mqtt_client_subscribe(s_client, s_topics.content_notify, 1);
        s_connected = true;
        if (s_events != NULL) {
            xEventGroupSetBits(s_events, MQTT_CONNECTED_BIT);
        }
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "disconnected from broker");
        s_connected = false;
        if (s_events != NULL) {
            xEventGroupClearBits(s_events, MQTT_CONNECTED_BIT);
        }
        break;
    case MQTT_EVENT_DATA:
        if (event->total_data_len > event->data_len) {
            ESP_LOGW(TAG, "fragmented inbound message, dropped");
            break;
        }
        handle_inbound(event->topic, event->topic_len, event->data,
                       event->data_len);
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGW(TAG, "MQTT transport error");
        s_connected = false;
        break;
    default:
        break;
    }
}

esp_err_t robot_mqtt_init(void)
{
    s_events = xEventGroupCreate();
    if (s_events == NULL) {
        return ESP_ERR_NO_MEM;
    }
    build_topics(&s_topics);
    return ESP_OK;
}

esp_err_t robot_mqtt_start(void)
{
    if (s_started) {
        return ESP_OK;
    }
    if (strlen(backend_device_token()) == 0) {
        ESP_LOGW(TAG, "no device auth material yet");
        return ESP_ERR_INVALID_STATE;
    }
    if (!wifi_is_connected()) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_mqtt_client_config_t cfg = {
        .broker.address.hostname = CONFIG_ROBOT_MQTT_HOST,
        .broker.address.port = CONFIG_ROBOT_MQTT_PORT,
        .credentials.username = identity_device_id(),
        .credentials.authentication.password = backend_device_token(),
        .session.keepalive = CONFIG_ROBOT_MQTT_KEEPALIVE_S,
        .session.last_will.topic = s_topics.status,
        .session.last_will.msg = LWT_PAYLOAD,
        .session.last_will.msg_len = (int)strlen(LWT_PAYLOAD),
        .session.last_will.qos = 1,
        .session.last_will.retain = true,
        .network.timeout_ms = 10000,
        .disable_auto_reconnect = true,
    };

    if (CONFIG_ROBOT_MQTT_USE_TLS) {
        cfg.broker.address.transport = MQTT_TRANSPORT_SECURE;
        if (strlen(CONFIG_ROBOT_MQTT_CA_PEM) > 0) {
            cfg.broker.verification.certificate = CONFIG_ROBOT_MQTT_CA_PEM;
        } else {
            cfg.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;
        }
    } else {
        cfg.broker.address.transport = MQTT_TRANSPORT_TCP;
        ESP_LOGW(TAG, "broker TLS disabled (lab mode only)");
    }

    s_client = esp_mqtt_client_init(&cfg);
    if (s_client == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID,
                                                   mqtt_event_handler, NULL);
    if (err != ESP_OK) {
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
        return err;
    }
    err = esp_mqtt_client_start(s_client);
    if (err != ESP_OK) {
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
        return err;
    }
    s_started = true;
    return ESP_OK;
}

void robot_mqtt_stop(void)
{
    if (s_client != NULL) {
        esp_mqtt_client_stop(s_client);
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
    }
    s_started = false;
    s_connected = false;
    if (s_events != NULL) {
        xEventGroupClearBits(s_events, MQTT_CONNECTED_BIT);
    }
}

bool robot_mqtt_connected(void)
{
    return s_connected;
}

bool robot_mqtt_wait_connected(uint32_t timeout_ms)
{
    if (s_events == NULL) {
        return false;
    }
    TickType_t ticks = (timeout_ms == 0) ? 0 : pdMS_TO_TICKS(timeout_ms);
    EventBits_t bits = xEventGroupWaitBits(s_events, MQTT_CONNECTED_BIT,
                                           pdFALSE, pdTRUE, ticks);
    return (bits & MQTT_CONNECTED_BIT) != 0;
}

static int publish_to(const char *topic, int qos, bool retain,
                      const char *payload, size_t len)
{
    if (!s_connected || s_client == NULL) {
        return -1;
    }
    return esp_mqtt_client_publish(s_client, topic, payload, (int)len, qos,
                                   retain ? 1 : 0);
}

int robot_mqtt_publish_status(const char *payload, size_t len)
{
    return publish_to(s_topics.status, 1, true, payload, len);
}

int robot_mqtt_publish_heartbeat(const char *payload, size_t len)
{
    return publish_to(s_topics.heartbeat, 0, false, payload, len);
}

int robot_mqtt_publish_event(const char *payload, size_t len)
{
    return publish_to(s_topics.event, 1, false, payload, len);
}
