#include "identity.hpp"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_random.h"
#include "mbedtls/base64.h"
#include "mbedtls/ctr_drbg.h"
#include "mbedtls/entropy.h"
#include "mbedtls/ecp.h"
#include "mbedtls/pk.h"
#include "mbedtls/sha256.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "identity";

#define NVS_NAMESPACE "robot"
#define NVS_KEY_DEVICE_ID "device_id"
#define NVS_KEY_PRIVATE_KEY "privkey"
#define NVS_KEY_REGISTERED "registered"

constexpr int DEVICE_ID_LEN = 37;
constexpr int PUB_PEM_SIZE = 512;
constexpr int PRIV_PEM_SIZE = 1024;
constexpr int SIG_MAX_SIZE = 128;

static mbedtls_pk_context s_pk;
static mbedtls_ctr_drbg_context s_drbg;
static mbedtls_entropy_context s_entropy;
static char s_device_id[DEVICE_ID_LEN];
static char s_pub_pem[PUB_PEM_SIZE];
static bool s_ready;

static int rng_cb(void *ctx, unsigned char *out, size_t len)
{
    (void)ctx;
    return mbedtls_ctr_drbg_random(&s_drbg, out, len) == 0 ? 0 : -1;
}

namespace {

class NvsScope
{
public:
    NvsScope(const char *nspace, nvs_open_mode_t mode)
        : m_handle(), m_err(nvs_open(nspace, mode, &m_handle))
    {
    }
    ~NvsScope()
    {
        if (m_err == ESP_OK) {
            nvs_close(m_handle);
        }
    }
    NvsScope(const NvsScope &) = delete;
    NvsScope &operator=(const NvsScope &) = delete;

    esp_err_t last_error() const { return m_err; }
    operator nvs_handle_t() const { return m_handle; }

private:
    nvs_handle_t m_handle;
    esp_err_t m_err;
};

}  // namespace

void identity_generate_uuid_v4(char *out, size_t out_len)
{
    uint8_t b[16];
    esp_fill_random(b, sizeof(b));
    b[6] = (uint8_t)((b[6] & 0x0f) | 0x40);
    b[8] = (uint8_t)((b[8] & 0x3f) | 0x80);
    snprintf(out, out_len,
             "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
             b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10],
             b[11], b[12], b[13], b[14], b[15]);
}

esp_err_t identity_init(void)
{
    if (s_ready) {
        return ESP_OK;
    }

    mbedtls_pk_init(&s_pk);
    mbedtls_ctr_drbg_init(&s_drbg);
    mbedtls_entropy_init(&s_entropy);

    int seed_rc = mbedtls_ctr_drbg_seed(&s_drbg, mbedtls_entropy_func,
                                        &s_entropy, NULL, 0);
    if (seed_rc != 0) {
        ESP_LOGE(TAG, "RNG init failed (%d)", seed_rc);
        return ESP_FAIL;
    }

    {
        NvsScope nvs(NVS_NAMESPACE, NVS_READWRITE);
        if (nvs.last_error() != ESP_OK) {
            ESP_LOGE(TAG, "NVS open failed (%s)",
                     esp_err_to_name(nvs.last_error()));
            return nvs.last_error();
        }

        size_t id_len = sizeof(s_device_id);
        bool have_id =
            nvs_get_str(nvs, NVS_KEY_DEVICE_ID, s_device_id, &id_len) ==
            ESP_OK;

        char priv_pem[PRIV_PEM_SIZE];
        size_t priv_len = sizeof(priv_pem);
        bool have_key =
            nvs_get_str(nvs, NVS_KEY_PRIVATE_KEY, priv_pem, &priv_len) ==
            ESP_OK;

        if (have_id && !have_key) {
            ESP_LOGE(TAG, "identity inconsistent (id without key), halted");
            memset(priv_pem, 0, sizeof(priv_pem));
            return ESP_FAIL;
        }
        if (!have_id && have_key) {
            ESP_LOGE(TAG, "identity inconsistent (key without id), halted");
            memset(priv_pem, 0, sizeof(priv_pem));
            return ESP_FAIL;
        }

        if (have_id && have_key) {
            int rc = mbedtls_pk_parse_key(&s_pk, (const unsigned char *)priv_pem,
                                          priv_len, NULL, 0, rng_cb, NULL);
            if (rc != 0) {
                ESP_LOGE(TAG, "stored key invalid (%d)", rc);
                memset(priv_pem, 0, sizeof(priv_pem));
                return ESP_FAIL;
            }
            memset(priv_pem, 0, sizeof(priv_pem));
            ESP_LOGI(TAG, "identity loaded from NVS");
        } else {
            identity_generate_uuid_v4(s_device_id, sizeof(s_device_id));

            int rc = mbedtls_pk_setup(
                &s_pk, mbedtls_pk_info_from_type(MBEDTLS_PK_ECKEY));
            if (rc != 0) {
                return ESP_FAIL;
            }
            rc = mbedtls_ecp_gen_key(MBEDTLS_ECP_DP_SECP256R1, mbedtls_pk_ec(s_pk),
                                     rng_cb, NULL);
            if (rc != 0) {
                ESP_LOGE(TAG, "keygen failed (%d)", rc);
                return ESP_FAIL;
            }
            rc = mbedtls_pk_write_key_pem(&s_pk, (unsigned char *)priv_pem,
                                          sizeof(priv_pem));
            if (rc != 0) {
                return ESP_FAIL;
            }
            esp_err_t err = nvs_set_str(nvs, NVS_KEY_DEVICE_ID, s_device_id);
            if (err == ESP_OK) {
                err = nvs_set_str(nvs, NVS_KEY_PRIVATE_KEY, priv_pem);
            }
            if (err == ESP_OK) {
                err = nvs_commit(nvs);
            }
            memset(priv_pem, 0, sizeof(priv_pem));
            if (err != ESP_OK) {
                return err;
            }
            ESP_LOGI(TAG, "new identity generated");
        }
    }

    int rc = mbedtls_pk_write_pubkey_pem(&s_pk, (unsigned char *)s_pub_pem,
                                         sizeof(s_pub_pem));
    if (rc != 0) {
        return ESP_FAIL;
    }

    s_ready = true;
    return ESP_OK;
}

const char *identity_device_id(void)
{
    return s_device_id;
}

const char *identity_public_key_pem(void)
{
    return s_pub_pem;
}

bool identity_is_registered(void)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return false;
    }
    uint8_t flag = 0;
    nvs_get_u8(nvs, NVS_KEY_REGISTERED, &flag);
    nvs_close(nvs);
    return flag == 1;
}

void identity_set_registered(bool registered)
{
    nvs_handle_t nvs;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) {
        return;
    }
    nvs_set_u8(nvs, NVS_KEY_REGISTERED, registered ? 1 : 0);
    nvs_commit(nvs);
    nvs_close(nvs);
}

esp_err_t identity_sign_challenge(const char *challenge, size_t challenge_len,
                                  char *sig_b64, size_t sig_b64_len,
                                  size_t *written)
{
    if (!s_ready || challenge == NULL || sig_b64 == NULL || written == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (challenge_len == 0 || challenge_len > 4096) {
        return ESP_ERR_INVALID_ARG;
    }

    unsigned char hash[32];
    int rc = mbedtls_sha256((const unsigned char *)challenge, challenge_len, hash,
                            0);
    if (rc != 0) {
        return ESP_FAIL;
    }

    unsigned char sig[SIG_MAX_SIZE];
    size_t sig_len = sizeof(sig);
    rc = mbedtls_pk_sign(&s_pk, MBEDTLS_MD_SHA256, hash, sizeof(hash), sig,
                         sizeof(sig), &sig_len, rng_cb, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "challenge sign failed (%d)", rc);
        return ESP_FAIL;
    }

    size_t olen = 0;
    rc = mbedtls_base64_encode((unsigned char *)sig_b64, sig_b64_len, &olen, sig,
                               sig_len);
    if (rc != 0 || olen + 1 > sig_b64_len) {
        return ESP_ERR_NO_MEM;
    }
    sig_b64[olen] = '\0';
    *written = olen;
    return ESP_OK;
}
