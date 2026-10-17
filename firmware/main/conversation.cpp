#include "conversation.hpp"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "audio_capture.hpp"
#include "backend_client.hpp"
#include "cJSON.h"
#include "conv_retry_after.h"
#include "conv_string.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "identity.hpp"
#include "sdkconfig.h"
#include "telemetry.hpp"

static const char *TAG = "conversation";

#define CONV_UPLOAD_MAX_FILE_BYTES (1024 * 1024)
#define CONV_UPLOAD_MAX_BODY_BYTES (1024 * 1024 + 16 * 1024)

#define CONV_MULTIPART_BOUNDARY "----RoboPanelAudioBoundary7f3a9c"
constexpr int CONV_BOUNDARY_LEN = 34;

#define CONV_UPLOAD_MAX_ATTEMPTS 3
#define CONV_UPLOAD_DEADLINE_MS 90000
#define CONV_RETRY_BACKOFF_BASE_MS 1000

constexpr int CONV_RESPONSE_MAX = 2048;
constexpr int CONV_PTT_POLL_MS = 20;
constexpr int CONV_PTT_DEBOUNCE_SAMPLES = 3;
constexpr int CONV_READ_CHUNK_BYTES = 2048;
constexpr int CONV_UUID_LEN = 37;
constexpr int CONV_SIDECAR_MAX = 192;

static const char *PREFIX_BOUNDARY_LINE = "--" CONV_MULTIPART_BOUNDARY "\r\n";
static const char *PREFIX_DISPOSITION =
    "Content-Disposition: form-data; name=\"audio\"; filename=\"clip.wav\"\r\n";
static const char *PREFIX_CONTENT_TYPE = "Content-Type: audio/wav\r\n\r\n";
static const char *SUFFIX = "\r\n--" CONV_MULTIPART_BOUNDARY "--\r\n";

static uint8_t *s_body_buf;
static size_t s_prefix_len;
static size_t s_suffix_len;

static bool language_hint_is_valid(const char *hint)
{
    return hint != NULL &&
           (strcmp(hint, "en") == 0 || strcmp(hint, "bn") == 0 ||
            strcmp(hint, "auto") == 0);
}

static bool ptt_pressed(void)
{
    int level = gpio_get_level(static_cast<gpio_num_t>(CONFIG_ROBOT_PTT_GPIO));
    return CONFIG_ROBOT_PTT_ACTIVE_LOW ? (level == 0) : (level != 0);
}

static const char *k_response_fields[] = {
    "conversation_id", "text",     "language",     "emotion",
    "action",          "safety",   "audio",        "memory_writes",
};
static const char *k_languages[] = {"en", "bn"};
static const char *k_emotions[] = {"HAPPY",  "CURIOUS",   "THINKING",
                                   "CONCERNED", "NEUTRAL"};
static const char *k_actions[] = {"WAVE", "NOD",   "TILT_HEAD", "IDLE",
                                  "POINT", "LISTENING_RETRY"};
static const char *k_safety_categories[] = {"PERSONAL_INFO_REQUEST",
                                            "UNSAFE_TOPIC"};
static const char *k_safety_actions[] = {"ALLOWED",
                                         "REDIRECTED",
                                         "BLOCKED",
                                         "ALLOWED_WITH_CAUTION"};
static const char *k_safety_fields[] = {"flagged", "category", "action_taken"};
static const char *k_memory_entry_fields[] = {"key", "value"};

static bool json_string_in_set(const cJSON *item, const char *const *set,
                               size_t n)
{
    if (!cJSON_IsString(item) || item->valuestring == NULL) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (strcmp(item->valuestring, set[i]) == 0) {
            return true;
        }
    }
    return false;
}

static bool json_nonempty_string(const cJSON *item)
{
    return cJSON_IsString(item) &&
           conv_string_has_nonwhitespace(item->valuestring);
}

static bool object_has_exact_keys(const cJSON *obj, const char *const *keys,
                                  size_t n)
{
    if (!cJSON_IsObject(obj) || (size_t)cJSON_GetArraySize(obj) != n) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (cJSON_GetObjectItemCaseSensitive(obj, keys[i]) == NULL) {
            return false;
        }
    }
    return true;
}

static bool validate_safety(const cJSON *safety)
{
    if (cJSON_IsNull(safety)) {
        return true;
    }
    if (!object_has_exact_keys(safety, k_safety_fields, 3)) {
        return false;
    }
    const cJSON *flagged = cJSON_GetObjectItemCaseSensitive(safety, "flagged");
    const cJSON *category = cJSON_GetObjectItemCaseSensitive(safety, "category");
    const cJSON *action_taken = cJSON_GetObjectItemCaseSensitive(safety, "action_taken");
    if (!cJSON_IsBool(flagged)) {
        return false;
    }
    if (!cJSON_IsNull(category) &&
        !json_string_in_set(category, k_safety_categories, 2)) {
        return false;
    }
    if (!json_string_in_set(action_taken, k_safety_actions, 4)) {
        return false;
    }
    if (cJSON_IsTrue(flagged) && cJSON_IsNull(category)) {
        return false;
    }
    return true;
}

static bool validate_memory_writes(const cJSON *writes)
{
    if (!cJSON_IsArray(writes)) {
        return false;
    }
    const cJSON *entry = NULL;
    cJSON_ArrayForEach(entry, writes)
    {
        if (!object_has_exact_keys(entry, k_memory_entry_fields, 2)) {
            return false;
        }
        if (!json_nonempty_string(cJSON_GetObjectItemCaseSensitive(entry, "key")) ||
            !json_nonempty_string(cJSON_GetObjectItemCaseSensitive(entry, "value"))) {
            return false;
        }
    }
    return true;
}

static bool response_is_valid(const char *json)
{
    if (json == NULL || json[0] == '\0') {
        return false;
    }
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        return false;
    }
    bool ok =
        object_has_exact_keys(root, k_response_fields, 8);
    if (ok) {
        ok = json_nonempty_string(cJSON_GetObjectItemCaseSensitive(root, "conversation_id")) &&
             json_nonempty_string(cJSON_GetObjectItemCaseSensitive(root, "text")) &&
             json_string_in_set(cJSON_GetObjectItemCaseSensitive(root, "language"),
                                k_languages, 2) &&
             json_string_in_set(cJSON_GetObjectItemCaseSensitive(root, "emotion"),
                                k_emotions, 5) &&
             json_string_in_set(cJSON_GetObjectItemCaseSensitive(root, "action"),
                                k_actions, 6) &&
             validate_safety(cJSON_GetObjectItemCaseSensitive(root, "safety")) &&
             (cJSON_IsNull(cJSON_GetObjectItemCaseSensitive(root, "audio")) ||
              json_nonempty_string(cJSON_GetObjectItemCaseSensitive(root, "audio"))) &&
             validate_memory_writes(
                 cJSON_GetObjectItemCaseSensitive(root, "memory_writes"));
    }
    cJSON_Delete(root);
    return ok;
}

static esp_err_t build_multipart(size_t *out_body_len)
{
    size_t file_len = AUDIO_WAV_FILE_MAX_BYTES;
    size_t total = s_prefix_len + file_len + s_suffix_len;
    if (file_len > CONV_UPLOAD_MAX_FILE_BYTES ||
        total > CONV_UPLOAD_MAX_BODY_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(s_body_buf, PREFIX_BOUNDARY_LINE, s_prefix_len);
    size_t tail = s_prefix_len + file_len;
    memcpy(s_body_buf + tail, SUFFIX, s_suffix_len);
    *out_body_len = tail + s_suffix_len;
    return ESP_OK;
}

static void run_upload(const char *sidecar, const char *idem_key,
                       size_t body_len)
{
    char response[CONV_RESPONSE_MAX];
    backend_audio_upload_meta_t meta;
    bool reauthenticated = false;
    const char *fail_reason = "transport";
    int64_t deadline_us =
        esp_timer_get_time() + (int64_t)CONV_UPLOAD_DEADLINE_MS * 1000;

    for (int attempt = 1; attempt <= CONV_UPLOAD_MAX_ATTEMPTS; attempt++) {
        if (esp_timer_get_time() >= deadline_us) {
            fail_reason = "deadline";
            break;
        }

        esp_err_t err = backend_upload_audio_turn(
            (const char *)s_body_buf, body_len, CONV_MULTIPART_BOUNDARY,
            sidecar, idem_key, response, sizeof(response), &meta);

        if (err != ESP_OK) {
            fail_reason = "transport";
        } else if (meta.status_code == 200) {
            if (meta.response_overflow || !response_is_valid(response)) {
                fail_reason = "response_invalid";
                if (attempt < CONV_UPLOAD_MAX_ATTEMPTS &&
                    esp_timer_get_time() < deadline_us) {
                    vTaskDelay(pdMS_TO_TICKS(CONV_RETRY_BACKOFF_BASE_MS *
                                             attempt));
                    continue;
                }
            } else {
                telemetry_publish_audio_upload_succeeded(
                    attempt, meta.idempotency_replayed);
                ESP_LOGI(TAG, "conversation turn complete (attempt=%d)",
                         attempt);
                return;
            }
        } else if (meta.status_code == 401) {
            fail_reason = "http_401";
            if (!reauthenticated && attempt < CONV_UPLOAD_MAX_ATTEMPTS) {
                if (backend_refresh_token() == ESP_OK) {
                    reauthenticated = true;
                    continue;
                }
            }
            break;
        } else if (meta.status_code == 429) {
            fail_reason = "http_429";
            int wait_s = conv_retry_after_wait_seconds(meta.retry_after_s);
            if (attempt < CONV_UPLOAD_MAX_ATTEMPTS &&
                esp_timer_get_time() < deadline_us) {
                vTaskDelay(pdMS_TO_TICKS((uint32_t)wait_s * 1000));
                continue;
            }
            break;
        } else if (meta.status_code == 503) {
            fail_reason = "http_503";
            if (attempt < CONV_UPLOAD_MAX_ATTEMPTS &&
                esp_timer_get_time() < deadline_us) {
                vTaskDelay(pdMS_TO_TICKS(CONV_RETRY_BACKOFF_BASE_MS *
                                         attempt));
                continue;
            }
            break;
        } else if (meta.status_code == 400) {
            fail_reason = "http_400";
            break;
        } else if (meta.status_code == 404) {
            fail_reason = "http_404";
            break;
        } else if (meta.status_code == 409) {
            fail_reason = "http_409";
            break;
        } else {
            fail_reason = "http_error";
            break;
        }

        if (attempt < CONV_UPLOAD_MAX_ATTEMPTS) {
            vTaskDelay(pdMS_TO_TICKS(CONV_RETRY_BACKOFF_BASE_MS * attempt));
        }
    }

    ESP_LOGW(TAG, "conversation turn failed (reason=%s)", fail_reason);
    telemetry_publish_audio_upload_failed(fail_reason);
}

static void run_turn(void)
{
    char idem_key[CONV_UUID_LEN];
    identity_generate_uuid_v4(idem_key, sizeof(idem_key));

    char sidecar[CONV_SIDECAR_MAX];
    int sidecar_len;
    if (language_hint_is_valid(CONFIG_ROBOT_LANGUAGE_HINT)) {
        sidecar_len = snprintf(sidecar, sizeof(sidecar),
                               "{\"child_profile_id\":\"%s\","
                               "\"language_hint\":\"%s\"}",
                               CONFIG_ROBOT_CHILD_PROFILE_ID,
                               CONFIG_ROBOT_LANGUAGE_HINT);
    } else {
        sidecar_len = snprintf(sidecar, sizeof(sidecar),
                               "{\"child_profile_id\":\"%s\"}",
                               CONFIG_ROBOT_CHILD_PROFILE_ID);
    }
    if (sidecar_len <= 0 || (size_t)sidecar_len >= sizeof(sidecar)) {
        telemetry_publish_audio_capture_failed("sidecar");
        telemetry_set_state("IDLE");
        return;
    }

    telemetry_set_state("CAPTURING");
    telemetry_publish_audio_capture_started();

    if (audio_capture_init() != ESP_OK) {
        telemetry_publish_audio_capture_failed("i2s_init");
        audio_capture_deinit();
        telemetry_set_state("IDLE");
        return;
    }

    size_t body_len = 0;
    if (build_multipart(&body_len) != ESP_OK) {
        telemetry_publish_audio_capture_failed("bounds");
        audio_capture_deinit();
        telemetry_set_state("IDLE");
        return;
    }

    uint8_t *pcm_dst = s_body_buf + s_prefix_len + AUDIO_WAV_HEADER_BYTES;
    size_t pcm_capacity = AUDIO_PCM_MAX_BYTES;
    size_t pcm_len = 0;
    int64_t started_us = esp_timer_get_time();
    int64_t max_us = (int64_t)AUDIO_CAPTURE_MAX_SECONDS * 1000000;
    const char *capture_fail = NULL;

    while (pcm_len < pcm_capacity) {
        if (!ptt_pressed()) {
            break;
        }
        int64_t elapsed = esp_timer_get_time() - started_us;
        if (elapsed >= max_us) {
            break;
        }
        size_t want = pcm_capacity - pcm_len;
        if (want > CONV_READ_CHUNK_BYTES) {
            want = CONV_READ_CHUNK_BYTES;
        }
        size_t got = 0;
        esp_err_t err =
            audio_capture_read_chunk(pcm_dst + pcm_len, want, &got, 1000);
        if (err != ESP_OK) {
            capture_fail = "i2s_read";
            break;
        }
        pcm_len += got;
    }

    audio_capture_deinit();

    if (capture_fail != NULL) {
        telemetry_publish_audio_capture_failed(capture_fail);
        telemetry_set_state("IDLE");
        return;
    }

    if (pcm_len < AUDIO_CAPTURE_MIN_PCM_BYTES) {
        telemetry_publish_audio_capture_failed("too_short");
        telemetry_set_state("IDLE");
        return;
    }

    audio_capture_fill_wav_header(s_body_buf + s_prefix_len,
                                  (uint32_t)pcm_len);
    size_t file_len = AUDIO_WAV_HEADER_BYTES + pcm_len;
    size_t total = s_prefix_len + file_len + s_suffix_len;
    if (file_len > CONV_UPLOAD_MAX_FILE_BYTES ||
        total > CONV_UPLOAD_MAX_BODY_BYTES) {
        telemetry_publish_audio_capture_failed("bounds");
        telemetry_set_state("IDLE");
        return;
    }
    memcpy(s_body_buf + s_prefix_len + file_len, SUFFIX, s_suffix_len);
    body_len = s_prefix_len + file_len + s_suffix_len;

    uint32_t duration_ms = (uint32_t)((esp_timer_get_time() - started_us) /
                                      1000);
    telemetry_publish_audio_capture_completed((uint32_t)pcm_len, duration_ms);

    telemetry_set_state("PROCESSING");
    run_upload(sidecar, idem_key, body_len);
    telemetry_set_state("IDLE");
}

static void ptt_task(void *arg)
{
    (void)arg;
    int stable = 0;
    bool was_pressed = false;

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(CONV_PTT_POLL_MS));
        if (ptt_pressed()) {
            stable++;
        } else {
            stable = 0;
        }
        bool pressed = stable >= CONV_PTT_DEBOUNCE_SAMPLES;
        if (pressed && !was_pressed) {
            run_turn();
            while (ptt_pressed()) {
                vTaskDelay(pdMS_TO_TICKS(CONV_PTT_POLL_MS));
            }
            stable = 0;
        }
        was_pressed = pressed;
    }
}

esp_err_t conversation_init(void)
{
    if (CONFIG_ROBOT_PTT_GPIO < 0) {
        ESP_LOGI(TAG, "PTT disabled by configuration");
        return ESP_OK;
    }
    if (!GPIO_IS_VALID_GPIO(CONFIG_ROBOT_PTT_GPIO)) {
        ESP_LOGW(TAG, "PTT gpio out of range, capture disabled");
        telemetry_publish_audio_unavailable("ptt_gpio_invalid");
        return ESP_ERR_INVALID_ARG;
    }
    if (CONFIG_ROBOT_CHILD_PROFILE_ID[0] == '\0') {
        ESP_LOGW(TAG, "capture disabled (profile not configured)");
        telemetry_publish_audio_unavailable("profile_not_configured");
        return ESP_ERR_INVALID_STATE;
    }
    if (CONFIG_ROBOT_MIC_I2S_BCLK < 0 || CONFIG_ROBOT_MIC_I2S_WS < 0 ||
        CONFIG_ROBOT_MIC_I2S_DIN < 0) {
        ESP_LOGW(TAG, "capture disabled (microphone pins unset)");
        telemetry_publish_audio_unavailable("mic_pins_unset");
        return ESP_ERR_INVALID_STATE;
    }
    if (!language_hint_is_valid(CONFIG_ROBOT_LANGUAGE_HINT)) {
        ESP_LOGW(TAG, "language hint not recognized, sidecar will omit it");
    }

    s_prefix_len = strlen(PREFIX_BOUNDARY_LINE) + strlen(PREFIX_DISPOSITION) +
                   strlen(PREFIX_CONTENT_TYPE);
    s_suffix_len = strlen(SUFFIX);
    size_t needed = s_prefix_len + AUDIO_WAV_FILE_MAX_BYTES + s_suffix_len;
    if (needed > CONV_UPLOAD_MAX_BODY_BYTES) {
        ESP_LOGW(TAG, "multipart bounds check failed, capture disabled");
        telemetry_publish_audio_unavailable("buffer_bounds");
        return ESP_ERR_INVALID_SIZE;
    }

    s_body_buf = static_cast<uint8_t *>(
        heap_caps_malloc(needed, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (s_body_buf == NULL) {
        ESP_LOGW(TAG, "conversation buffer allocation failed");
        telemetry_publish_audio_unavailable("buffer_alloc");
        return ESP_ERR_NO_MEM;
    }

    gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_ROBOT_PTT_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = CONFIG_ROBOT_PTT_ACTIVE_LOW ? GPIO_PULLUP_ENABLE
                                                  : GPIO_PULLUP_DISABLE,
        .pull_down_en = CONFIG_ROBOT_PTT_ACTIVE_LOW ? GPIO_PULLDOWN_DISABLE
                                                    : GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "PTT gpio config failed (%s)", esp_err_to_name(err));
        heap_caps_free(s_body_buf);
        s_body_buf = NULL;
        telemetry_publish_audio_unavailable("ptt_gpio");
        return err;
    }

    BaseType_t ok = xTaskCreate(ptt_task, "robot_ptt", 6144, NULL, 5, NULL);
    if (ok != pdPASS) {
        heap_caps_free(s_body_buf);
        s_body_buf = NULL;
        telemetry_publish_audio_unavailable("ptt_task");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "conversation capture ready");
    return ESP_OK;
}
