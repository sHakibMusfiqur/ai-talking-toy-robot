#include "audio_capture.h"

#include <string.h>

#include "driver/i2s_std.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "sdkconfig.h"

static const char *TAG = "audio_capture";

static i2s_chan_handle_t s_rx_chan;
static bool s_ready;

static void write_u16le(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)(value & 0xff);
    dst[1] = (uint8_t)((value >> 8) & 0xff);
}

static void write_u32le(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)(value & 0xff);
    dst[1] = (uint8_t)((value >> 8) & 0xff);
    dst[2] = (uint8_t)((value >> 16) & 0xff);
    dst[3] = (uint8_t)((value >> 24) & 0xff);
}

esp_err_t audio_capture_init(void)
{
    if (s_ready) {
        return ESP_OK;
    }
    if (CONFIG_ROBOT_MIC_I2S_BCLK < 0 || CONFIG_ROBOT_MIC_I2S_WS < 0 ||
        CONFIG_ROBOT_MIC_I2S_DIN < 0) {
        return ESP_ERR_INVALID_STATE;
    }

    i2s_chan_config_t chan_cfg =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "i2s channel alloc failed (%s)", esp_err_to_name(err));
        return err;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_CAPTURE_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg =
            {
                .mclk = I2S_GPIO_UNUSED,
                .bclk = CONFIG_ROBOT_MIC_I2S_BCLK,
                .ws = CONFIG_ROBOT_MIC_I2S_WS,
                .dout = I2S_GPIO_UNUSED,
                .din = CONFIG_ROBOT_MIC_I2S_DIN,
            },
    };

    err = i2s_channel_init_std_mode(s_rx_chan, &std_cfg);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "i2s std mode init failed (%s)", esp_err_to_name(err));
        i2s_del_channel(s_rx_chan);
        s_rx_chan = NULL;
        return err;
    }

    err = i2s_channel_enable(s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "i2s enable failed (%s)", esp_err_to_name(err));
        i2s_del_channel(s_rx_chan);
        s_rx_chan = NULL;
        return err;
    }

    s_ready = true;
    return ESP_OK;
}

bool audio_capture_ready(void)
{
    return s_ready;
}

esp_err_t audio_capture_read_chunk(uint8_t *dst, size_t bytes,
                                   size_t *out_read, uint32_t timeout_ms)
{
    if (out_read != NULL) {
        *out_read = 0;
    }
    if (!s_ready || dst == NULL || bytes == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    size_t read = 0;
    esp_err_t err =
        i2s_channel_read(s_rx_chan, dst, bytes, &read, pdMS_TO_TICKS(timeout_ms));
    if (out_read != NULL) {
        *out_read = read;
    }
    return err;
}

void audio_capture_deinit(void)
{
    if (!s_ready) {
        return;
    }
    i2s_channel_disable(s_rx_chan);
    i2s_del_channel(s_rx_chan);
    s_rx_chan = NULL;
    s_ready = false;
}

void audio_capture_fill_wav_header(uint8_t *hdr44, uint32_t pcm_bytes)
{
    if (hdr44 == NULL) {
        return;
    }
    const uint16_t channels = AUDIO_CAPTURE_CHANNELS;
    const uint16_t bits = AUDIO_CAPTURE_BITS_PER_SAMPLE;
    const uint32_t rate = AUDIO_CAPTURE_SAMPLE_RATE_HZ;
    const uint16_t block_align =
        (uint16_t)(channels * (AUDIO_CAPTURE_BITS_PER_SAMPLE / 8));
    const uint32_t byte_rate = rate * block_align;

    memset(hdr44, 0, AUDIO_WAV_HEADER_BYTES);
    memcpy(hdr44 + 0, "RIFF", 4);
    write_u32le(hdr44 + 4, 36 + pcm_bytes);
    memcpy(hdr44 + 8, "WAVE", 4);
    memcpy(hdr44 + 12, "fmt ", 4);
    write_u32le(hdr44 + 16, 16);
    write_u16le(hdr44 + 20, 1);
    write_u16le(hdr44 + 22, channels);
    write_u32le(hdr44 + 24, rate);
    write_u32le(hdr44 + 28, byte_rate);
    write_u16le(hdr44 + 32, block_align);
    write_u16le(hdr44 + 34, bits);
    memcpy(hdr44 + 36, "data", 4);
    write_u32le(hdr44 + 40, pcm_bytes);
}
