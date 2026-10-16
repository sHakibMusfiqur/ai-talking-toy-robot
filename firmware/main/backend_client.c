#include "backend_client.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "conv_retry_after.h"
#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "identity.h"
#include "sdkconfig.h"

static const char *TAG = "backend_client";

#define HTTP_RESPONSE_MAX 4096
#define TOKEN_MAX 2048
#define CHALLENGE_MAX 160
#define CHALLENGE_ID_MAX 64
#define SIGNATURE_B64_MAX 256

#define CONV_AUDIO_UPLOAD_PATH "/api/v1/conversations/turn/audio"
#define CONV_UPLOAD_MAX_BODY_BYTES (1024 * 1024 + 16 * 1024)
#define CONV_UPLOAD_TIMEOUT_MS 20000

#define DEVICE_CLAIM_TICKETS_PATH "/api/v1/devices/claim-tickets/"
#define CLAIM_URL_PATH "/onboarding/claim/"

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    int status_code;
    bool overflow;
    bool idempotency_replayed;
    int retry_after_s;
} http_response_t;

static char s_token[TOKEN_MAX];
static int s_token_expires_in;

static esp_err_t http_event_handler(esp_http_client_event_t *evt)
{
    http_response_t *resp = (http_response_t *)evt->user_data;
    if (evt->event_id == HTTP_EVENT_ON_DATA) {
        if (resp->buf == NULL) {
            return ESP_OK;
        }
        if (resp->len + evt->data_len >= resp->cap) {
            resp->overflow = true;
            return ESP_OK;
        }
        memcpy(resp->buf + resp->len, evt->data, evt->data_len);
        resp->len += evt->data_len;
        resp->buf[resp->len] = '\0';
    } else if (evt->event_id == HTTP_EVENT_ON_FINISH) {
        resp->status_code = esp_http_client_get_status_code(evt->client);
    } else if (evt->event_id == HTTP_EVENT_HEADERS) {
        if (evt->header_key == NULL || evt->header_value == NULL) {
            return ESP_OK;
        }
        if (strcasecmp(evt->header_key, "Retry-After") == 0) {
            resp->retry_after_s = conv_parse_retry_after(evt->header_value);
        } else if (strcasecmp(evt->header_key, "Idempotency-Replayed") == 0) {
            resp->idempotency_replayed =
                strcasecmp(evt->header_value, "true") == 0;
        }
    }
    return ESP_OK;
}

static esp_err_t build_url(char *out, size_t out_len, const char *path)
{
    const char *scheme = CONFIG_ROBOT_BACKEND_USE_TLS ? "https" : "http";
    int written = snprintf(out, out_len, "%s://%s:%d%s", scheme,
                           CONFIG_ROBOT_BACKEND_HOST,
                           CONFIG_ROBOT_BACKEND_HTTP_PORT, path);
    if (written <= 0 || (size_t)written >= out_len) {
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

static esp_err_t post_json(const char *path, const char *body,
                           const char *auth_token, int *status_out,
                           char *resp_out, size_t resp_out_len)
{
    char url[256];
    esp_err_t err = build_url(url, sizeof(url), path);
    if (err != ESP_OK) {
        return err;
    }

    http_response_t resp = {
        .buf = resp_out,
        .cap = resp_out_len,
        .len = 0,
        .status_code = 0,
        .overflow = false,
        .idempotency_replayed = false,
        .retry_after_s = -1,
    };
    if (resp_out != NULL && resp_out_len > 0) {
        resp_out[0] = '\0';
    }

    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .event_handler = http_event_handler,
        .user_data = &resp,
        .timeout_ms = 15000,
        .buffer_size = 2048,
    };
    if (CONFIG_ROBOT_BACKEND_USE_TLS) {
        if (strlen(CONFIG_ROBOT_BACKEND_CA_PEM) > 0) {
            cfg.cert_pem = CONFIG_ROBOT_BACKEND_CA_PEM;
        } else {
            cfg.crt_bundle_attach = esp_crt_bundle_attach;
        }
    } else {
        ESP_LOGW(TAG, "backend TLS disabled (lab mode only)");
    }

    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    if (auth_token != NULL && auth_token[0] != '\0') {
        char auth_header[TOKEN_MAX + 16];
        snprintf(auth_header, sizeof(auth_header), "Bearer %s", auth_token);
        esp_http_client_set_header(client, "Authorization", auth_header);
        memset(auth_header, 0, sizeof(auth_header));
    }
    esp_http_client_set_post_field(client, body, strlen(body));

    err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        if (resp.status_code == 0) {
            resp.status_code = esp_http_client_get_status_code(client);
        }
        if (status_out != NULL) {
            *status_out = resp.status_code;
        }
        if (resp.overflow) {
            err = ESP_ERR_INVALID_SIZE;
        }
    } else {
        ESP_LOGW(TAG, "request to %s failed (%s)", path, esp_err_to_name(err));
    }
    esp_http_client_cleanup(client);
    return err;
}

static char *object_body(const char *key1, const char *val1, const char *key2,
                         const char *val2, const char *key3, const char *val3)
{
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return NULL;
    }
    cJSON_AddStringToObject(root, key1, val1);
    if (key2 != NULL) {
        cJSON_AddStringToObject(root, key2, val2);
    }
    if (key3 != NULL) {
        cJSON_AddStringToObject(root, key3, val3);
    }
    char *body = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return body;
}

esp_err_t backend_client_init(void)
{
    return ESP_OK;
}

esp_err_t backend_register(char *pairing_code_out, size_t pairing_code_len)
{
    char *body = object_body("device_id", identity_device_id(), "public_key",
                             identity_public_key_pem(), "firmware_version",
                             esp_app_get_description()->version);
    if (body == NULL) {
        return ESP_ERR_NO_MEM;
    }

    static char response[HTTP_RESPONSE_MAX];
    int status = 0;
    esp_err_t err = post_json("/api/v1/devices/register/", body, NULL, &status,
                              response, sizeof(response));
    cJSON_free(body);
    if (err != ESP_OK) {
        return err;
    }

    if (status == 409) {
        identity_set_registered(true);
        ESP_LOGI(TAG, "device already registered");
        return ESP_OK;
    }
    if (status != 201) {
        ESP_LOGW(TAG, "registration rejected (HTTP %d)", status);
        return ESP_FAIL;
    }

    cJSON *parsed = cJSON_Parse(response);
    if (parsed == NULL) {
        return ESP_FAIL;
    }
    const cJSON *code = cJSON_GetObjectItem(parsed, "pairing_code");
    if (pairing_code_out != NULL && pairing_code_len > 0 &&
        cJSON_IsString(code)) {
        strlcpy(pairing_code_out, code->valuestring, pairing_code_len);
    }
    cJSON_Delete(parsed);
    identity_set_registered(true);
    return ESP_OK;
}

esp_err_t backend_refresh_token(void)
{
    char *body = object_body("device_id", identity_device_id(), NULL, NULL,
                             NULL, NULL);
    if (body == NULL) {
        return ESP_ERR_NO_MEM;
    }

    static char response[HTTP_RESPONSE_MAX];
    int status = 0;
    esp_err_t err =
        post_json("/api/v1/devices/auth/challenge/", body, NULL, &status,
                  response, sizeof(response));
    cJSON_free(body);
    if (err != ESP_OK || status != 200) {
        ESP_LOGW(TAG, "challenge request failed (HTTP %d)", status);
        return ESP_FAIL;
    }

    char challenge[CHALLENGE_MAX];
    char challenge_id[CHALLENGE_ID_MAX];
    cJSON *parsed = cJSON_Parse(response);
    if (parsed == NULL) {
        return ESP_FAIL;
    }
    const cJSON *challenge_item = cJSON_GetObjectItem(parsed, "challenge");
    const cJSON *id_item = cJSON_GetObjectItem(parsed, "challenge_id");
    bool ok = cJSON_IsString(challenge_item) && cJSON_IsString(id_item);
    if (ok) {
        strlcpy(challenge, challenge_item->valuestring, sizeof(challenge));
        strlcpy(challenge_id, id_item->valuestring, sizeof(challenge_id));
    }
    cJSON_Delete(parsed);
    if (!ok) {
        return ESP_FAIL;
    }

    char signature[SIGNATURE_B64_MAX];
    size_t sig_len = 0;
    err = identity_sign_challenge(challenge, strlen(challenge), signature,
                                  sizeof(signature), &sig_len);
    if (err != ESP_OK) {
        return err;
    }

    cJSON *token_obj = cJSON_CreateObject();
    if (token_obj == NULL) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddStringToObject(token_obj, "device_id", identity_device_id());
    cJSON_AddStringToObject(token_obj, "challenge_id", challenge_id);
    cJSON_AddStringToObject(token_obj, "challenge", challenge);
    cJSON_AddStringToObject(token_obj, "signature", signature);
    body = cJSON_PrintUnformatted(token_obj);
    cJSON_Delete(token_obj);
    if (body == NULL) {
        return ESP_ERR_NO_MEM;
    }

    err = post_json("/api/v1/devices/auth/token/", body, NULL, &status,
                    response, sizeof(response));
    cJSON_free(body);
    if (err != ESP_OK || status != 200) {
        ESP_LOGW(TAG, "auth exchange failed (HTTP %d)", status);
        return ESP_FAIL;
    }

    parsed = cJSON_Parse(response);
    if (parsed == NULL) {
        return ESP_FAIL;
    }
    const cJSON *access_token = cJSON_GetObjectItem(parsed, "access_token");
    const cJSON *expires_in = cJSON_GetObjectItem(parsed, "expires_in");
    if (!cJSON_IsString(access_token)) {
        cJSON_Delete(parsed);
        return ESP_FAIL;
    }
    strlcpy(s_token, access_token->valuestring, sizeof(s_token));
    s_token_expires_in = cJSON_IsNumber(expires_in) ? expires_in->valueint : 3600;
    cJSON_Delete(parsed);
    ESP_LOGI(TAG, "device auth acquired");
    return ESP_OK;
}

const char *backend_device_token(void)
{
    return s_token;
}

int backend_token_expires_in(void)
{
    return s_token_expires_in;
}

esp_err_t backend_create_claim_ticket(char *ticket_out, size_t ticket_len)
{
    if (ticket_out == NULL || ticket_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    ticket_out[0] = '\0';
    if (s_token[0] == '\0') {
        return ESP_ERR_INVALID_STATE;
    }

    for (int attempt = 0; attempt < 2; ++attempt) {
        cJSON *root = cJSON_CreateObject();
        if (root == NULL) {
            return ESP_ERR_NO_MEM;
        }
        char *body = cJSON_PrintUnformatted(root);
        cJSON_Delete(root);
        if (body == NULL) {
            return ESP_ERR_NO_MEM;
        }

        static char response[HTTP_RESPONSE_MAX];
        int status = 0;
        esp_err_t err = post_json(DEVICE_CLAIM_TICKETS_PATH, body, s_token,
                                  &status, response, sizeof(response));
        cJSON_free(body);
        if (err != ESP_OK) {
            return err;
        }
        if (status == 401 && attempt == 0) {
            if (backend_refresh_token() != ESP_OK) {
                return ESP_FAIL;
            }
            continue;
        }
        if (status != 201) {
            ESP_LOGW(TAG, "claim ticket request rejected (HTTP %d)", status);
            return ESP_FAIL;
        }

        cJSON *parsed = cJSON_Parse(response);
        if (parsed == NULL) {
            return ESP_FAIL;
        }
        const cJSON *ticket = cJSON_GetObjectItem(parsed, "ticket");
        bool ok = cJSON_IsString(ticket) && ticket->valuestring[0] != '\0';
        if (ok) {
            strlcpy(ticket_out, ticket->valuestring, ticket_len);
        }
        cJSON_Delete(parsed);
        if (!ok) {
            return ESP_FAIL;
        }
        ESP_LOGI(TAG, "claim ticket issued");
        return ESP_OK;
    }
    return ESP_FAIL;
}

esp_err_t backend_claim_url(const char *ticket, char *out, size_t out_len)
{
    if (ticket == NULL || ticket[0] == '\0' || out == NULL || out_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    char path[160];
    int written =
        snprintf(path, sizeof(path), CLAIM_URL_PATH "?ticket=%s", ticket);
    if (written <= 0 || (size_t)written >= sizeof(path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return build_url(out, out_len, path);
}

esp_err_t backend_upload_audio_turn(const char *body, size_t body_len,
                                    const char *boundary,
                                    const char *sidecar_json,
                                    const char *idempotency_key,
                                    char *resp_out, size_t resp_out_len,
                                    backend_audio_upload_meta_t *meta)
{
    if (meta != NULL) {
        meta->status_code = 0;
        meta->transport_failed = false;
        meta->response_overflow = false;
        meta->idempotency_replayed = false;
        meta->retry_after_s = -1;
    }
    if (body == NULL || body_len == 0 || body_len > CONV_UPLOAD_MAX_BODY_BYTES ||
        boundary == NULL || sidecar_json == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    char url[256];
    esp_err_t err = build_url(url, sizeof(url), CONV_AUDIO_UPLOAD_PATH);
    if (err != ESP_OK) {
        return err;
    }

    char content_type[128];
    int written = snprintf(content_type, sizeof(content_type),
                           "multipart/form-data; boundary=%s", boundary);
    if (written <= 0 || (size_t)written >= sizeof(content_type)) {
        return ESP_ERR_INVALID_SIZE;
    }

    http_response_t resp = {
        .buf = resp_out,
        .cap = resp_out_len,
        .len = 0,
        .status_code = 0,
        .overflow = false,
        .idempotency_replayed = false,
        .retry_after_s = -1,
    };
    if (resp_out != NULL && resp_out_len > 0) {
        resp_out[0] = '\0';
    }

    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .event_handler = http_event_handler,
        .user_data = &resp,
        .timeout_ms = CONV_UPLOAD_TIMEOUT_MS,
        .buffer_size = 2048,
    };
    if (CONFIG_ROBOT_BACKEND_USE_TLS) {
        if (strlen(CONFIG_ROBOT_BACKEND_CA_PEM) > 0) {
            cfg.cert_pem = CONFIG_ROBOT_BACKEND_CA_PEM;
        } else {
            cfg.crt_bundle_attach = esp_crt_bundle_attach;
        }
    } else {
        ESP_LOGW(TAG, "backend TLS disabled (lab mode only)");
    }

    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }

    char auth_header[TOKEN_MAX + 16];
    snprintf(auth_header, sizeof(auth_header), "Bearer %s", s_token);
    esp_http_client_set_header(client, "Authorization", auth_header);
    esp_http_client_set_header(client, "Content-Type", content_type);
    esp_http_client_set_header(client, "Conversation-Sidecar", sidecar_json);
    if (idempotency_key != NULL && idempotency_key[0] != '\0') {
        esp_http_client_set_header(client, "Idempotency-Key", idempotency_key);
    }
    esp_http_client_set_post_field(client, body, (int)body_len);

    err = esp_http_client_perform(client);
    memset(auth_header, 0, sizeof(auth_header));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "audio upload transport failed (%s)",
                 esp_err_to_name(err));
        esp_http_client_cleanup(client);
        if (meta != NULL) {
            meta->transport_failed = true;
        }
        return err;
    }

    if (meta != NULL) {
        meta->status_code = resp.status_code;
        meta->response_overflow = resp.overflow;
        meta->idempotency_replayed = resp.idempotency_replayed;
        meta->retry_after_s = resp.retry_after_s;
    }
    if (resp.overflow) {
        ESP_LOGW(TAG, "audio upload response exceeded buffer");
    }
    esp_http_client_cleanup(client);
    return ESP_OK;
}
