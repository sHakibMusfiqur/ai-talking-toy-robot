#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t identity_init(void);
const char *identity_device_id(void);
const char *identity_public_key_pem(void);
bool identity_is_registered(void);
void identity_set_registered(bool registered);
esp_err_t identity_sign_challenge(const char *challenge, size_t challenge_len,
                                  char *sig_b64, size_t sig_b64_len,
                                  size_t *written);
