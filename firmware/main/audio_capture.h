#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define AUDIO_CAPTURE_SAMPLE_RATE_HZ 16000
#define AUDIO_CAPTURE_BITS_PER_SAMPLE 16
#define AUDIO_CAPTURE_CHANNELS 1
#define AUDIO_CAPTURE_MAX_SECONDS 15
#define AUDIO_PCM_MAX_BYTES \
    (AUDIO_CAPTURE_SAMPLE_RATE_HZ * (AUDIO_CAPTURE_BITS_PER_SAMPLE / 8) * \
     AUDIO_CAPTURE_CHANNELS * AUDIO_CAPTURE_MAX_SECONDS)
#define AUDIO_WAV_HEADER_BYTES 44
#define AUDIO_WAV_FILE_MAX_BYTES (AUDIO_WAV_HEADER_BYTES + AUDIO_PCM_MAX_BYTES)

esp_err_t audio_capture_init(void);
bool audio_capture_ready(void);
esp_err_t audio_capture_read_chunk(uint8_t *dst, size_t bytes,
                                   size_t *out_read, uint32_t timeout_ms);
void audio_capture_deinit(void);
void audio_capture_fill_wav_header(uint8_t *hdr44, uint32_t pcm_bytes);
