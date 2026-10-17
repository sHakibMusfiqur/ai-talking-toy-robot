#include "actions.hpp"

#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "robot_mqtt.hpp"
#include "telemetry.hpp"

static const char *TAG = "actions";

constexpr int ACTION_MAX = 65;
constexpr int COMMAND_RAW_MAX = 160;
constexpr int COMMAND_QUEUE_DEPTH = 4;
constexpr int STEP_DELAY_MS = 200;
constexpr int NEUTRAL_ARM_DEG = 0;
constexpr int NEUTRAL_HEAD_DEG = 0;

static const char *const kSemanticActions[] = {
    "IDLE",
    "WAVE",
    "DANCE",
    "NOD",
    "TILT_HEAD",
    "POINT",
    "LISTENING_RETRY",
};

constexpr size_t SEMANTIC_ACTION_COUNT =
    sizeof(kSemanticActions) / sizeof(kSemanticActions[0]);

typedef struct {
    const char *joint;
    int min_deg;
    int max_deg;
} joint_limits_t;

static const joint_limits_t kJointLimits[] = {
    {"head_pan", -45, 45},
    {"head_tilt", -30, 30},
    {"left_arm", 0, 180},
    {"right_arm", 0, 180},
};

constexpr size_t JOINT_LIMIT_COUNT =
    sizeof(kJointLimits) / sizeof(kJointLimits[0]);

typedef struct {
    const char *joint;
    int target_deg;
} move_step_t;

static const move_step_t kIdleProgram[] = {
    {"head_pan", NEUTRAL_HEAD_DEG},
    {"head_tilt", NEUTRAL_HEAD_DEG},
    {"left_arm", NEUTRAL_ARM_DEG},
    {"right_arm", NEUTRAL_ARM_DEG},
};

static const move_step_t kWaveProgram[] = {
    {"right_arm", 90},
    {"right_arm", 140},
    {"right_arm", 90},
    {"right_arm", 140},
    {"right_arm", NEUTRAL_ARM_DEG},
};

static const move_step_t kDanceProgram[] = {
    {"left_arm", 120},
    {"right_arm", 120},
    {"head_pan", -30},
    {"left_arm", 60},
    {"right_arm", 60},
    {"head_pan", 30},
    {"left_arm", NEUTRAL_ARM_DEG},
    {"right_arm", NEUTRAL_ARM_DEG},
    {"head_pan", NEUTRAL_HEAD_DEG},
};

static const move_step_t kNodProgram[] = {
    {"head_tilt", 20},
    {"head_tilt", -20},
    {"head_tilt", 20},
    {"head_tilt", NEUTRAL_HEAD_DEG},
};

static const move_step_t kTiltHeadProgram[] = {
    {"head_pan", 40},
    {"head_pan", NEUTRAL_HEAD_DEG},
};

static const move_step_t kPointProgram[] = {
    {"right_arm", 90},
    {"right_arm", NEUTRAL_ARM_DEG},
};

static const move_step_t kListeningRetryProgram[] = {
    {"head_pan", -25},
    {"head_pan", 25},
    {"head_pan", NEUTRAL_HEAD_DEG},
};

typedef struct {
    char raw[COMMAND_RAW_MAX];
} command_item_t;

static QueueHandle_t s_command_queue;

constexpr int EVENT_JSON_MAX_LOCAL = 512;

static int clamp_to_limits(const char *joint, int target_deg)
{
    for (size_t i = 0; i < JOINT_LIMIT_COUNT; i++) {
        if (strcmp(kJointLimits[i].joint, joint) == 0) {
            if (target_deg < kJointLimits[i].min_deg) {
                return kJointLimits[i].min_deg;
            }
            if (target_deg > kJointLimits[i].max_deg) {
                return kJointLimits[i].max_deg;
            }
            return target_deg;
        }
    }
    ESP_LOGW(TAG, "unknown joint '%s', ignored", joint);
    return INT32_MIN;
}

static void apply_move(const char *joint, int target_deg)
{
    int clamped = clamp_to_limits(joint, target_deg);
    if (clamped == INT32_MIN) {
        return;
    }
    ESP_LOGI(TAG, "move %s -> %d deg", joint, clamped);
}

static void run_program(const move_step_t *steps, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        apply_move(steps[i].joint, steps[i].target_deg);
        vTaskDelay(pdMS_TO_TICKS(STEP_DELAY_MS));
    }
}

static void publish_command_event(const char *event_type, const char *action,
                                  const char *reason)
{
    char payload[160];
    if (reason != NULL) {
        snprintf(payload, sizeof(payload),
                 "{\"action\":\"%.64s\",\"reason\":\"%.64s\"}",
                 action != NULL ? action : "", reason);
    } else {
        snprintf(payload, sizeof(payload), "{\"action\":\"%.64s\"}",
                 action != NULL ? action : "");
    }
    char json[EVENT_JSON_MAX_LOCAL];
    int written = snprintf(json, sizeof(json),
                           "{\"event_type\":\"%s\",\"payload\":%s}",
                           event_type, payload);
    if (written <= 0 || (size_t)written >= sizeof(json)) {
        return;
    }
    if (robot_mqtt_publish_event(json, (size_t)written) < 0) {
        ESP_LOGW(TAG, "command event %s not delivered", event_type);
    }
}

static const char *match_semantic_action(const char *action, size_t len)
{
    if (len == 0 || len >= ACTION_MAX) {
        return NULL;
    }
    for (size_t i = 0; i < SEMANTIC_ACTION_COUNT; i++) {
        const char *candidate = kSemanticActions[i];
        if (strlen(candidate) != len) {
            continue;
        }
        bool same = true;
        for (size_t j = 0; j < len; j++) {
            if (toupper((unsigned char)action[j]) !=
                toupper((unsigned char)candidate[j])) {
                same = false;
                break;
            }
        }
        if (same) {
            return candidate;
        }
    }
    return NULL;
}

static void execute_action(const char *action)
{
    ESP_LOGI(TAG, "Received semantic action: %s", action);
    publish_command_event("command.received", action, NULL);
    telemetry_set_state(action);

    if (strcmp(action, "IDLE") == 0) {
        run_program(kIdleProgram,
                    sizeof(kIdleProgram) / sizeof(kIdleProgram[0]));
    } else if (strcmp(action, "WAVE") == 0) {
        run_program(kWaveProgram, sizeof(kWaveProgram) / sizeof(kWaveProgram[0]));
    } else if (strcmp(action, "DANCE") == 0) {
        run_program(kDanceProgram,
                    sizeof(kDanceProgram) / sizeof(kDanceProgram[0]));
    } else if (strcmp(action, "NOD") == 0) {
        run_program(kNodProgram, sizeof(kNodProgram) / sizeof(kNodProgram[0]));
    } else if (strcmp(action, "TILT_HEAD") == 0) {
        run_program(kTiltHeadProgram,
                    sizeof(kTiltHeadProgram) / sizeof(kTiltHeadProgram[0]));
    } else if (strcmp(action, "POINT") == 0) {
        run_program(kPointProgram,
                    sizeof(kPointProgram) / sizeof(kPointProgram[0]));
    } else if (strcmp(action, "LISTENING_RETRY") == 0) {
        run_program(kListeningRetryProgram,
                    sizeof(kListeningRetryProgram) /
                        sizeof(kListeningRetryProgram[0]));
    }

    publish_command_event("command.executed", action, NULL);
    telemetry_set_state("IDLE");
}

static void process_command(const char *raw, size_t len)
{
    (void)len;
    cJSON *parsed = cJSON_Parse(raw);
    if (parsed == NULL) {
        publish_command_event("command.rejected", NULL, "invalid_payload");
        return;
    }
    const cJSON *action_item = cJSON_GetObjectItem(parsed, "action");
    if (!cJSON_IsString(action_item)) {
        cJSON_Delete(parsed);
        publish_command_event("command.rejected", NULL, "invalid_payload");
        return;
    }
    const char *canonical =
        match_semantic_action(action_item->valuestring,
                              strlen(action_item->valuestring));
    if (canonical == NULL) {
        publish_command_event("command.rejected", action_item->valuestring,
                              "unknown_action");
        cJSON_Delete(parsed);
        return;
    }
    cJSON_Delete(parsed);
    execute_action(canonical);
}

static void actions_task(void *arg)
{
    (void)arg;
    command_item_t item;
    for (;;) {
        if (xQueueReceive(s_command_queue, &item, portMAX_DELAY) == pdTRUE) {
            process_command(item.raw, strlen(item.raw));
        }
    }
}

void actions_handle_command(const char *payload, size_t len)
{
    if (payload == NULL || s_command_queue == NULL) {
        return;
    }
    command_item_t item = {0};
    size_t copy = len < sizeof(item.raw) - 1 ? len : sizeof(item.raw) - 1;
    memcpy(item.raw, payload, copy);
    item.raw[copy] = '\0';
    if (xQueueSend(s_command_queue, &item, 0) != pdTRUE) {
        ESP_LOGW(TAG, "command queue full, command dropped");
    }
}

esp_err_t actions_init(void)
{
    s_command_queue = xQueueCreate(COMMAND_QUEUE_DEPTH, sizeof(command_item_t));
    if (s_command_queue == NULL) {
        return ESP_ERR_NO_MEM;
    }
    BaseType_t ok =
        xTaskCreate(actions_task, "robot_actions", 6144, NULL, 6, NULL);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
