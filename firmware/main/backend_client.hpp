#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

typedef struct {
    int status_code;
    bool transport_failed;
    bool response_overflow;
    bool idempotency_replayed;
    int retry_after_s;
} backend_audio_upload_meta_t;

esp_err_t backend_client_init(void);
esp_err_t backend_register(char *pairing_code_out, size_t pairing_code_len);
esp_err_t backend_refresh_token(void);
const char *backend_device_token(void);
int backend_token_expires_in(void);
esp_err_t backend_create_claim_ticket(char *ticket_out, size_t ticket_len);
esp_err_t backend_claim_url(const char *ticket, char *out, size_t out_len);
esp_err_t backend_upload_audio_turn(const char *body, size_t body_len,
                                    const char *boundary,
                                    const char *sidecar_json,
                                    const char *idempotency_key,
                                    char *resp_out, size_t resp_out_len,
                                    backend_audio_upload_meta_t *meta);
