#include "wifi_provision.hpp"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "backend_client.hpp"
#include "cJSON.h"
#include "driver/gpio.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "identity.hpp"
#include "nvs.h"
#include "sdkconfig.h"
#include "wifi.hpp"

static const char *TAG = "provision";

extern const uint8_t setup_html_start[] asm("_binary_setup_html_start");
extern const uint8_t setup_html_end[] asm("_binary_setup_html_end");

constexpr int PROV_TOKEN_HEX_LEN = 32;
constexpr int PROV_HEADER_MAX = 40;
constexpr int SCAN_MAX_RESULTS = 16;
constexpr int CONNECT_TIMEOUT_MS = 30000;
constexpr int AUTH_FAILURE_DECISION_COUNT = 2;
constexpr int CLAIM_TICKET_MAX = 128;
constexpr int CLAIM_URL_MAX = 320;
constexpr int REQUEST_BODY_MAX = 512;
constexpr int FAIL_REASON_MAX = 16;

#define NVS_NAMESPACE "wifi"
#define NVS_KEY_SETUP_PASS "setup_pass"

typedef enum {
    PROV_STATE_IDLE,
    PROV_STATE_CONNECTING,
    PROV_STATE_READY,
    PROV_STATE_FAILED,
} provision_state_t;

static httpd_handle_t s_server;
static SemaphoreHandle_t s_lock;
static provision_state_t s_state = PROV_STATE_IDLE;
static char s_fail_reason[FAIL_REASON_MAX];
static char s_claim_url[CLAIM_URL_MAX];
static bool s_has_claim_url;
static bool s_finalizing;

static char s_pending_ssid[33];
static char s_pending_pass[65];
static volatile uint32_t s_auth_baseline;

static char s_provision_token[PROV_TOKEN_HEX_LEN + 1];
static char s_setup_ssid[33];
static char s_setup_key[65];
static char s_start_reason[24] = "no_credentials";
static char s_saved_ssid[33];

static void provision_worker(void *arg);

static void halt_loop(void)
{
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

static void set_failed(const char *reason)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    strlcpy(s_fail_reason, reason, sizeof(s_fail_reason));
    s_state = PROV_STATE_FAILED;
    xSemaphoreGive(s_lock);
    memset(s_pending_pass, 0, sizeof(s_pending_pass));
    ESP_LOGW(TAG, "setup attempt failed (%s)", reason);
}

static void set_ready(const char *url, bool has_url)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    strlcpy(s_claim_url, (url != NULL) ? url : "", sizeof(s_claim_url));
    s_has_claim_url = has_url;
    s_state = PROV_STATE_READY;
    xSemaphoreGive(s_lock);
    ESP_LOGI(TAG, "setup complete (claim link %s)", has_url ? "ready"
                                                            : "deferred");
}

static void setup_ap_identity(void)
{
    const char *dev = identity_device_id();
    size_t dev_len = strlen(dev);
    const char *suffix = (dev_len > 4) ? dev + dev_len - 4 : dev;
    snprintf(s_setup_ssid, sizeof(s_setup_ssid), "%s-%s",
             CONFIG_ROBOT_SETUP_AP_SSID_PREFIX, suffix);

    if (strlen(CONFIG_ROBOT_SETUP_AP_PASSWORD) >= 8) {
        strlcpy(s_setup_key, CONFIG_ROBOT_SETUP_AP_PASSWORD,
                sizeof(s_setup_key));
        return;
    }

    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) {
        ESP_LOGE(TAG, "cannot open setup credentials store");
        halt_loop();
    }
    size_t len = sizeof(s_setup_key);
    esp_err_t err = nvs_get_str(handle, NVS_KEY_SETUP_PASS, s_setup_key,
                                &len);
    if (err != ESP_OK || strlen(s_setup_key) < 8) {
        static const char charset[] =
            "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
        uint8_t rnd[16];
        esp_fill_random(rnd, sizeof(rnd));
        for (size_t i = 0; i < 12; ++i) {
            s_setup_key[i] = charset[rnd[i] % (sizeof(charset) - 1)];
        }
        s_setup_key[12] = '\0';
        esp_err_t persist_err = nvs_set_str(handle, NVS_KEY_SETUP_PASS,
                                            s_setup_key);
        if (persist_err == ESP_OK) {
            persist_err = nvs_commit(handle);
        }
        if (persist_err != ESP_OK) {
            ESP_LOGW(TAG,
                     "setup key not persisted (%s); new key next boot",
                     esp_err_to_name(persist_err));
        }
    }
    nvs_close(handle);
}

static void print_setup_banner(void)
{
    printf(
        "\n==================================================\n"
        " Robot setup network (join from your phone):\n"
        "   Wi-Fi:     %s\n"
        "   Setup key: %s\n"
        "   Page:      http://192.168.4.1\n"
        "   (open it manually if it does not appear)\n"
        "==================================================\n\n",
        s_setup_ssid, s_setup_key);
}

static char *render_page(size_t *out_len)
{
    static const char T_PH[] = "__PROVISION_TOKEN__";
    static const char R_PH[] = "__PROVISION_REASON__";
    static const char S_PH[] = "__SAVED_SSID__";
    const char *phs[3] = {T_PH, R_PH, S_PH};
    const char *vals[3];
    size_t ph_lens[3];
    size_t val_lens[3];
    size_t counts[3] = {0, 0, 0};

    const uint8_t *html = setup_html_start;
    size_t html_len = (size_t)(setup_html_end - setup_html_start);

    vals[0] = s_provision_token;
    vals[1] = s_start_reason;
    vals[2] = s_saved_ssid;

    int64_t total = (int64_t)html_len;
    for (int i = 0; i < 3; ++i) {
        ph_lens[i] = strlen(phs[i]);
        val_lens[i] = strlen(vals[i]);
    }
    for (size_t i = 0; i < html_len;) {
        bool matched = false;
        for (int j = 0; j < 3; ++j) {
            if (i + ph_lens[j] <= html_len &&
                memcmp(html + i, phs[j], ph_lens[j]) == 0) {
                total += (int64_t)val_lens[j] - (int64_t)ph_lens[j];
                counts[j]++;
                i += ph_lens[j];
                matched = true;
                break;
            }
        }
        if (!matched) {
            i++;
        }
    }
    for (int i = 0; i < 3; ++i) {
        if (counts[i] == 0) {
            ESP_LOGE(TAG, "setup page placeholder missing: %s", phs[i]);
            return NULL;
        }
    }
    if (total <= 0) {
        return NULL;
    }

    char *out = static_cast<char *>(malloc((size_t)total + 1));
    if (out == NULL) {
        return NULL;
    }

    size_t oi = 0;
    for (size_t i = 0; i < html_len;) {
        bool matched = false;
        for (int j = 0; j < 3; ++j) {
            if (i + ph_lens[j] <= html_len &&
                memcmp(html + i, phs[j], ph_lens[j]) == 0) {
                memcpy(out + oi, vals[j], val_lens[j]);
                oi += val_lens[j];
                i += ph_lens[j];
                matched = true;
                break;
            }
        }
        if (!matched) {
            out[oi++] = (char)html[i++];
        }
    }
    out[oi] = '\0';
    *out_len = oi;
    return out;
}

static bool token_ok(httpd_req_t *req)
{
    size_t len = httpd_req_get_hdr_value_len(req, "X-Provision-Token");
    if (len == 0 || len > PROV_HEADER_MAX) {
        return false;
    }
    char buf[PROV_HEADER_MAX + 1] = {0};
    if (httpd_req_get_hdr_value_str(req, "X-Provision-Token", buf,
                                    sizeof(buf)) != ESP_OK) {
        return false;
    }
    return strcmp(buf, s_provision_token) == 0;
}

static esp_err_t handle_root(httpd_req_t *req)
{
    size_t page_len = 0;
    char *page = render_page(&page_len);
    if (page == NULL) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "render failed");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_set_hdr(req, "X-Content-Type-Options", "nosniff");
    esp_err_t err = httpd_resp_send(req, page, page_len);
    free(page);
    return err;
}

static esp_err_t handle_scan(httpd_req_t *req)
{
    wifi_scan_config_t scan_cfg = {
        .scan_time = {.active = {.min = 100, .max = 400}},
    };
    if (esp_wifi_scan_start(&scan_cfg, true) != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "scan failed");
        return ESP_FAIL;
    }

    static wifi_ap_record_t records[SCAN_MAX_RESULTS];
    uint16_t count = SCAN_MAX_RESULTS;
    if (esp_wifi_scan_get_ap_records(&count, records) != ESP_OK) {
        count = 0;
    }

    cJSON *arr = cJSON_CreateArray();
    if (arr == NULL) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
        return ESP_FAIL;
    }
    for (uint16_t i = 0; i < count; ++i) {
        if (records[i].ssid[0] == '\0') {
            continue;
        }
        bool duplicate = false;
        for (uint16_t j = 0; j < i; ++j) {
            if (strcmp((const char *)records[i].ssid,
                       (const char *)records[j].ssid) == 0) {
                duplicate = true;
                break;
            }
        }
        if (!duplicate) {
            cJSON_AddItemToArray(arr, cJSON_CreateString((const char *)records[i].ssid));
        }
    }
    char *body = cJSON_PrintUnformatted(arr);
    cJSON_Delete(arr);
    if (body == NULL) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "application/json");
    esp_err_t err = httpd_resp_send(req, body, HTTPD_RESP_USE_STRLEN);
    cJSON_free(body);
    return err;
}

static bool read_json_body(httpd_req_t *req, char *buf, size_t buf_len)
{
    size_t content_len = req->content_len;
    if (content_len == 0 || content_len >= buf_len) {
        return false;
    }
    size_t received = 0;
    while (received < content_len) {
        int n = httpd_req_recv(req, buf + received, content_len - received);
        if (n <= 0) {
            return false;
        }
        received += (size_t)n;
    }
    buf[received] = '\0';
    return true;
}

static esp_err_t handle_wifi_submit(httpd_req_t *req)
{
    if (!token_ok(req)) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_set_status(req, "403 Forbidden");
        return httpd_resp_send(req, "{\"error\":\"bad_token\"}",
                               HTTPD_RESP_USE_STRLEN);
    }

    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool busy = (s_state == PROV_STATE_CONNECTING);
    xSemaphoreGive(s_lock);
    if (busy || s_finalizing) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_set_status(req, "409 Conflict");
        return httpd_resp_send(req, "{\"error\":\"busy\"}",
                               HTTPD_RESP_USE_STRLEN);
    }

    static char body[REQUEST_BODY_MAX];
    if (!read_json_body(req, body, sizeof(body))) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_set_status(req, "400 Bad Request");
        return httpd_resp_send(req, "{\"error\":\"invalid_body\"}",
                               HTTPD_RESP_USE_STRLEN);
    }

    cJSON *parsed = cJSON_ParseWithLength(body, strlen(body));
    memset(body, 0, sizeof(body));
    if (parsed == NULL) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_set_status(req, "400 Bad Request");
        return httpd_resp_send(req, "{\"error\":\"invalid_json\"}",
                               HTTPD_RESP_USE_STRLEN);
    }
    const cJSON *ssid_item = cJSON_GetObjectItem(parsed, "ssid");
    const cJSON *pass_item = cJSON_GetObjectItem(parsed, "password");
    bool ssid_ok = cJSON_IsString(ssid_item) && ssid_item->valuestring[0] != '\0' &&
                   strlen(ssid_item->valuestring) < sizeof(s_pending_ssid);
    bool pass_ok = cJSON_IsString(pass_item) && strlen(pass_item->valuestring) >= 8 &&
                   strlen(pass_item->valuestring) < sizeof(s_pending_pass);
    if (!ssid_ok || !pass_ok) {
        cJSON_Delete(parsed);
        httpd_resp_set_type(req, "application/json");
        httpd_resp_set_status(req, "400 Bad Request");
        return httpd_resp_send(
            req, "{\"error\":\"ssid_and_wpa2_password_required\"}",
            HTTPD_RESP_USE_STRLEN);
    }
    strlcpy(s_pending_ssid, ssid_item->valuestring, sizeof(s_pending_ssid));
    strlcpy(s_pending_pass, pass_item->valuestring, sizeof(s_pending_pass));
    cJSON_Delete(parsed);

    s_auth_baseline = wifi_auth_failure_count();
    xSemaphoreTake(s_lock, portMAX_DELAY);
    strlcpy(s_fail_reason, "", sizeof(s_fail_reason));
    strlcpy(s_claim_url, "", sizeof(s_claim_url));
    s_has_claim_url = false;
    s_state = PROV_STATE_CONNECTING;
    xSemaphoreGive(s_lock);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, "{\"ok\":true}", HTTPD_RESP_USE_STRLEN);

    esp_err_t err = wifi_sta_connect_with(s_pending_ssid, s_pending_pass);
    if (err != ESP_OK) {
        set_failed("connect_start");
        return ESP_OK;
    }
    if (xTaskCreate(provision_worker, "prov_worker", 8192, NULL, 5, NULL) !=
        pdPASS) {
        set_failed("worker_spawn");
    }
    return ESP_OK;
}

static bool finish_with_backend(char *url_out, size_t url_len,
                                char *code_out, size_t code_len)
{
    if (!identity_is_registered()) {
        if (backend_register(code_out, code_len) != ESP_OK) {
            return false;
        }
    }
    if (strlen(backend_device_token()) == 0) {
        if (backend_refresh_token() != ESP_OK) {
            return false;
        }
    }
    char ticket[CLAIM_TICKET_MAX] = {0};
    if (backend_create_claim_ticket(ticket, sizeof(ticket)) != ESP_OK) {
        return false;
    }
    bool ok = backend_claim_url(ticket, url_out, url_len) == ESP_OK;
    memset(ticket, 0, sizeof(ticket));
    return ok;
}

static void provision_worker(void *arg)
{
    (void)arg;
    uint32_t baseline = s_auth_baseline;
    int64_t deadline =
        esp_timer_get_time() + (int64_t)CONNECT_TIMEOUT_MS * 1000;
    bool connected = false;
    for (;;) {
        if (wifi_is_connected()) {
            connected = true;
            break;
        }
        if (wifi_auth_failure_count() >=
            baseline + AUTH_FAILURE_DECISION_COUNT) {
            break;
        }
        if (esp_timer_get_time() >= deadline) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(200));
    }

    if (!connected) {
        if (wifi_auth_failure_count() >=
            baseline + AUTH_FAILURE_DECISION_COUNT) {
            set_failed("auth");
        } else {
            set_failed("no_ap");
        }
        return;
    }

    if (!wifi_save_credentials(s_pending_ssid, s_pending_pass)) {
        set_failed("storage");
        return;
    }
    memset(s_pending_pass, 0, sizeof(s_pending_pass));

    char url[CLAIM_URL_MAX] = {0};
    char legacy_code[16] = {0};
    bool has_url =
        finish_with_backend(url, sizeof(url), legacy_code,
                            sizeof(legacy_code));
    if (!has_url && legacy_code[0] != '\0') {
        printf(
            "\n==================================================\n"
            " Robot connected. Pairing code (single use):\n"
            "   %s\n"
            " Enter it in the web dashboard to link this robot.\n"
            "==================================================\n\n",
            legacy_code);
    }
    set_ready(url, has_url);
}

static esp_err_t handle_status(httpd_req_t *req)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    provision_state_t state_copy = s_state;
    const char *state = "idle";
    switch (state_copy) {
    case PROV_STATE_IDLE:
        state = "idle";
        break;
    case PROV_STATE_CONNECTING:
        state = "connecting";
        break;
    case PROV_STATE_READY:
        state = "ready";
        break;
    case PROV_STATE_FAILED:
        state = "failed";
        break;
    }
    char url[CLAIM_URL_MAX];
    strlcpy(url, s_claim_url, sizeof(url));
    bool has_url = s_has_claim_url;
    char reason[FAIL_REASON_MAX];
    strlcpy(reason, s_fail_reason, sizeof(reason));
    xSemaphoreGive(s_lock);

    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
        return ESP_FAIL;
    }
    cJSON_AddStringToObject(root, "state", state);
    if (state_copy == PROV_STATE_FAILED) {
        cJSON_AddStringToObject(root, "reason", reason);
    }
    if (has_url && url[0] != '\0') {
        cJSON_AddStringToObject(root, "claim_url", url);
    }
    char *body = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (body == NULL) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "oom");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "application/json");
    esp_err_t err = httpd_resp_send(req, body, HTTPD_RESP_USE_STRLEN);
    cJSON_free(body);
    return err;
}

static esp_err_t handle_finalize(httpd_req_t *req)
{
    if (!token_ok(req)) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_set_status(req, "403 Forbidden");
        return httpd_resp_send(req, "{\"error\":\"bad_token\"}",
                               HTTPD_RESP_USE_STRLEN);
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool ready = (s_state == PROV_STATE_READY);
    bool already = s_finalizing;
    if (ready && !already) {
        s_finalizing = true;
    }
    xSemaphoreGive(s_lock);

    if (!ready || already) {
        httpd_resp_set_type(req, "application/json");
        httpd_resp_set_status(req, "409 Conflict");
        return httpd_resp_send(req, "{\"error\":\"not_ready\"}",
                               HTTPD_RESP_USE_STRLEN);
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, "{\"ok\":true}", HTTPD_RESP_USE_STRLEN);

    ESP_LOGI(TAG, "restarting into station mode");
    vTaskDelay(pdMS_TO_TICKS(700));
    esp_restart();
    return ESP_OK;
}

static void setup_indication_init(void)
{
    if (CONFIG_ROBOT_SETUP_LED_GPIO < 0) {
        return;
    }
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_ROBOT_SETUP_LED_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&io) == ESP_OK) {
        gpio_set_level(static_cast<gpio_num_t>(CONFIG_ROBOT_SETUP_LED_GPIO), 1);
        ESP_LOGI(TAG, "setup indicator on GPIO %d",
                 CONFIG_ROBOT_SETUP_LED_GPIO);
    }
}

static void start_httpd(void)
{
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.stack_size = 8192;
    cfg.max_uri_handlers = 8;
    cfg.lru_purge_enable = true;
    if (httpd_start(&s_server, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "setup web server failed to start");
        halt_loop();
    }

    const httpd_uri_t root = {.uri = "/",
                              .method = HTTP_GET,
                              .handler = handle_root};
    const httpd_uri_t scan = {.uri = "/api/scan",
                              .method = HTTP_GET,
                              .handler = handle_scan};
    const httpd_uri_t submit = {.uri = "/api/wifi",
                                .method = HTTP_POST,
                                .handler = handle_wifi_submit};
    const httpd_uri_t status = {.uri = "/api/status",
                                .method = HTTP_GET,
                                .handler = handle_status};
    const httpd_uri_t finalize = {.uri = "/api/finalize",
                                  .method = HTTP_POST,
                                  .handler = handle_finalize};
    httpd_register_uri_handler(s_server, &root);
    httpd_register_uri_handler(s_server, &scan);
    httpd_register_uri_handler(s_server, &submit);
    httpd_register_uri_handler(s_server, &status);
    httpd_register_uri_handler(s_server, &finalize);
}

void provision_run(const char *reason, bool wifi_already_started)
{
    strlcpy(s_start_reason, (reason != NULL) ? reason : "no_credentials",
            sizeof(s_start_reason));
    ESP_LOGW(TAG, "entering setup mode (%s)", s_start_reason);

    if (strcmp(s_start_reason, "auth_failure") == 0) {
        (void)wifi_get_saved_ssid(s_saved_ssid, sizeof(s_saved_ssid));
    }

    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        halt_loop();
    }

    if (wifi_init_stack() != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi stack init failed");
        halt_loop();
    }
    wifi_set_autoreconnect(false);
    setup_ap_identity();
    esp_netif_create_default_wifi_ap();

    uint8_t rnd[PROV_TOKEN_HEX_LEN / 2];
    esp_fill_random(rnd, sizeof(rnd));
    for (size_t i = 0; i < sizeof(rnd); ++i) {
        snprintf(s_provision_token + i * 2, 3, "%02x", rnd[i]);
    }

    if (wifi_already_started) {
        (void)esp_wifi_stop();
    }

    wifi_config_t ap_cfg = {0};
    strlcpy((char *)ap_cfg.ap.ssid, s_setup_ssid, sizeof(ap_cfg.ap.ssid));
    ap_cfg.ap.ssid_len = strlen(s_setup_ssid);
    strlcpy((char *)ap_cfg.ap.password, s_setup_key,
            sizeof(ap_cfg.ap.password));
    ap_cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ap_cfg.ap.max_connection = 4;

    wifi_config_t sta_cfg = {0};

    esp_err_t wifi_err = esp_wifi_set_mode(WIFI_MODE_APSTA);
    if (wifi_err == ESP_OK) {
        wifi_err = esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
    }
    if (wifi_err == ESP_OK) {
        wifi_err = esp_wifi_set_config(WIFI_IF_STA, &sta_cfg);
    }
    if (wifi_err == ESP_OK) {
        wifi_err = esp_wifi_start();
    }
    if (wifi_err != ESP_OK) {
        ESP_LOGE(TAG, "provisioning Wi-Fi start failed (%s), halted",
                 esp_err_to_name(wifi_err));
        halt_loop();
    }

    start_httpd();
    setup_indication_init();
    print_setup_banner();

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
