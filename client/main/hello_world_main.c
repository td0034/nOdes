#include <string.h>
#include <stdio.h>
#include <math.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <errno.h>
#include <unistd.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"

#include "driver/gpio.h"
#include "driver/gptimer.h"
#include "driver/spi_master.h"
#include "driver/i2c.h"
#include "driver/i2s_std.h"

#include "lwip/sockets.h"

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "esp_timer.h"
#include "esp_sleep.h"
#include "esp_pm.h"
#include "esp_mac.h"
#include "esp_private/wifi.h"

#include "ina219.h"

// OTA includes
#include "esp_https_ota.h"
#include "esp_ota_ops.h"
#include "esp_http_client.h"
#include "esp_app_desc.h"

// ESP-NOW includes
#include "esp_now.h"

// -------------------- Config --------------------
#define OTA_URL "http://10.0.0.8:8000/espidf_orb.bin"
#define VERSION(major, minor) ((major<<8)|minor)

// ************************************************
// UPDATE THIS EVERY VERSION!!!
// 3.24 (de-saturate RSSI *255/100) is PARKED, not abandoned: on the bench it
// made clusters harder to achieve (the adaptive-gap valley moves), so the
// fleet was unified back on 3.23 on 2026-08-11 pending a deliberate A/B of
// the two mappings. To rebuild 3.24: strength mapping *255/100 + VERSION(3,24).
#define ORBVERSION VERSION(3,23)   // top-PROX_N=10 RSSI report, saturating strength mapping (fleet baseline)
// ************************************************

#define WIFI_SSID "OrbAP"
#define WIFI_PASS "CHANGE_ME"

#define MULTICAST_ADDR "239.255.0.1"
#define MULTICAST_PORT 5000
#define SERVER_IP "10.0.0.8"

// Delay before return packet
#define DELAY_US 8000

// ESP-NOW proximity
#define MAX_ORBS 32
#define CMD_PROX_ON  0x02
#define CMD_PROX_OFF 0x03
#define CMD_SLEEP    0x05
#define CMD_MIC_ON       0x06
#define CMD_SELF_LED_ON  0x07
#define CMD_SELF_LED_OFF 0x08
#define ESPNOW_TAG "ESP-NOW"

// OTA trigger command - you can send this via UDP to trigger OTA
#define OTA_TRIGGER_CMD "OTA_UPDATE"

// APA102 Configuration
#define NUM_LEDS 12
#define DATA_PIN 18
#define CLK_PIN 8
#define APA102_BRIGHTNESS 128

// LSM6DS Configuration
#define LSM_INT_1 21 // interrupt pin to wake esp with on shake

// Battery Management
#define BATTERY_MIN 3.1       // Red - low battery threshold
#define BATTERY_MID 3.95      // Green (midpoint)
#define BATTERY_MAX 4.2       // Blue - max voltage
#define BATTERY_90_PERCENT 4.1 // 90% threshold
#define CHARGING_THRESHOLD_MA 10.0 // Current above this = charging
#define NOT_CHARGING_THRESHOLD_MA 5.0 // Current below this = not charging

// Sleep Management
#define SLEEP_TIMEOUT_MS 30000 // 30 seconds
#define DEEP_SLEEP_DURATION_US 5000000 // 5 seconds in microseconds

// Accelerometer streaming configuration
#define ACCEL_SAMPLE_RATE_HZ 50 // 50Hz sampling rate
#define ACCEL_STREAM_PORT 5001  // Different port for accelerometer data

// Audio configuration constants
#define AMP_SAMPLE_RATE 44100
#define PI 3.14159265
#define BUFFER_SIZE 256
#define TONE_DURATION_MS 80     // Slightly longer
#define COLOR_CHANGE_DEBOUNCE_MS 150 // Minimum time between notes

// I2S amp output pins
#define AMP_DIN 40
#define AMP_BCLK 41
#define AMP_LRCK 42

// I2S mic input pins (ICS-43434 on I2S1)
#define MIC_SD   16
#define MIC_SCK  17
#define MIC_WS   15
#define MIC_SAMPLE_RATE 16000
#define MIC_TAG "MIC"

static const char *TAG = "udp_multicast";
static const char *OTA_TAG = "OTA_UPDATE";
static const char *PIXEL_TAG = "APA102";
static const char *BATTERY_TAG = "BATTERY";
static const char *SLEEP_TAG = "SLEEP";
static const char *ACCEL_TAG = "ACCELEROMETER";
static const char *AUDIO_TAG = "AUDIO";

// -------- OTA state (comprehensive handling) --------
static volatile bool ota_in_progress = false;       // gates network & LEDs
static TaskHandle_t ota_task_handle = NULL;         // ensure single OTA task

// Optional: simple OTA status LED color (purple) and helpers
typedef struct { uint8_t r; uint8_t g; uint8_t b; } rgb_color_t;
static inline rgb_color_t color_ota(void) { return (rgb_color_t){128, 0, 128}; }
static inline rgb_color_t color_black(void) { return (rgb_color_t){0, 0, 0}; }

static void ota_quiet_begin(void);
static void ota_quiet_end(void);

// Wi-Fi + timing
static EventGroupHandle_t wifi_event_group;
#define WIFI_CONNECTED_BIT    BIT0
#define WIFI_DISCONNECTED_BIT BIT1
#define WIFI_STUCK_LED_THRESHOLD 4
#define WIFI_HARD_RESET_THRESHOLD 8
static volatile int wifi_retry_count = 0;
typedef enum { WIFI_LED_OFF, WIFI_LED_SEARCHING, WIFI_LED_STUCK } wifi_led_state_t;
static volatile wifi_led_state_t wifi_led_state = WIFI_LED_OFF;
#define GPIO_TIMED 6
#define GPIO_BROADCAST 7
static gptimer_handle_t timer;
static SemaphoreHandle_t send_semaphore = NULL;
static int64_t arrival_time;
static int64_t last_arrival_time;

// Online/watchdog
static volatile int online_watchdog = 0;
#define ONLINE_WATCHDOG_SILENT_THRESHOLD 10  // 10 * 100ms = 1s of no multicast

// Slot allocation
static volatile int slot_alloc = -1;

// Unique 24 bit serial
static uint32_t serial;

// Packet send timer
static int64_t send_start;
static int64_t send_done;
static int64_t send_time;
static int send_size;

// ESP-NOW proximity state
static bool espnow_initialized = false;
static bool proximity_enabled = false;
static int8_t rssi_matrix[MAX_ORBS];
static int64_t rssi_last_seen[MAX_ORBS]; // microsecond timestamp of last ESP-NOW RX per slot
#define RSSI_STALE_US 3000000 // 3 seconds — clear peers not heard for this long

// NxN mutual strength matrix. peer_matrix[a][b] = what orb a reports about b
// (0..255 strength, 0 = unknown/absent). Populated from the top-6 payloads
// carried in each orb's ESP-NOW broadcast, so each orb locally holds the same
// picture the server assembles from unicast reports. Enables study-branch
// clustering without a server.
#define ESPNOW_FRAME_SIZE 17  // slot(1)+serial(3)+nn(1)+{slot,strength}x6(12)
static uint8_t peer_matrix[MAX_ORBS][MAX_ORBS];

// Number of strongest RSSI neighbours reported to the server (E1 ablation: was
// fixed at 6; build 8/10/12 to test whether >6-7 improves clustering). Only the
// SERVER-UPLOAD report scales with this; the inter-orb ESP-NOW frame stays at 6
// so the fleet's frame format is unchanged. Server reads however many arrive
// (n_prox = (packet_size-64)/2), so top-6 and top-N orbs coexist.
#define PROX_N 10
static int64_t peer_matrix_last_seen[MAX_ORBS];
static SemaphoreHandle_t rssi_mutex = NULL;

// Self-assigned slot with Knuth multiplicative hash + collision-driven offset.
// Used only when the server hasn't allocated a slot yet.
static volatile int self_slot_offset = 0;
static volatile int collision_count = 0;
static int64_t last_collision_bump_us = 0;

static inline int self_slot_base(void) {
    // Top 5 bits of serial * golden-ratio prime — better spread than serial & 0x1F.
    return (int)(((uint32_t)serial * 0x9E3779B1u) >> 27);
}

static inline int self_slot_effective(void) {
    return (self_slot_base() + self_slot_offset) & 0x1F;
}

// Called from recv callback when another orb is broadcasting under our slot.
// Higher-serial orb backs off (deterministic, no live-lock). Rate-limited to
// one bump per 500ms so we don't thrash if a third orb is also bumping.
static void maybe_resolve_slot_collision(int peer_slot, uint32_t peer_serial) {
    if (slot_alloc >= 0 && slot_alloc < MAX_ORBS) return;  // server owns our slot
    if (peer_serial == serial) return;
    if (peer_slot != self_slot_effective()) return;
    if (serial <= peer_serial) return;  // other orb wins the tie-break, we keep the slot

    int64_t now = esp_timer_get_time();
    if (now - last_collision_bump_us < 500000) return;
    last_collision_bump_us = now;

    self_slot_offset = (self_slot_offset + 1) & 0x1F;
    collision_count++;
    ESP_LOGW(ESPNOW_TAG, "Slot collision with %06lx — offset -> %d, new slot=%d (collisions=%d)",
             (unsigned long)peer_serial, self_slot_offset, self_slot_effective(), collision_count);
}
static SemaphoreHandle_t espnow_send_sem = NULL;
static esp_timer_handle_t espnow_timer = NULL;
static esp_timer_handle_t standalone_espnow_timer = NULL;  // periodic pacer used while server is silent
static const uint8_t espnow_broadcast_addr[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Packet structures
typedef struct {
    uint32_t packet_num;
    uint32_t command;
    uint32_t slot_alloc;
    // Padding to position led_colour array starting at byte 64
    uint32_t padding[16];
    uint16_t led_colour[32];
} Send_packet;

// Proximity data entry: packed (slot_id, strength) pair
typedef struct {
    uint8_t slot_id;
    uint8_t strength; // 0-255 normalized
} __attribute__((packed)) proximity_entry_t;

typedef struct {
    uint32_t slot_alloc; // bits 24-31 are allocated slot, 0xff if no alloc recently
    float accel_x;
    float accel_y;
    float accel_z;
    int rssi;
    int rx_time;
    int tx_time;
    float batt_v;
    float batt_i;
    uint32_t pkt_latency;
    float temperature; // 40 - 43
    uint32_t sequence;
    uint32_t revision; // Use top byte to signal receipt of OTA
    float gyro_x;
    float gyro_y;
    float gyro_z;
    // RSSI proximity: top PROX_N strongest ESP-NOW peers (bytes 64 onward)
    proximity_entry_t rssi_proximity[PROX_N];
} Recv_packet;

const int spacket_size = 128;
const int rpacket_size = 64 + 2 * PROX_N;   // 64-byte head + PROX_N (slot,strength) pairs
static Recv_packet r_packet;

// APA102 variables
static spi_device_handle_t spi_device;
static rgb_color_t leds[NUM_LEDS];

// Accelerometer data structure for UDP streaming
typedef struct {
    int64_t timestamp_us;
    float ax;
    float ay;
    float az;
    uint32_t sample_count;
} accel_data_packet_t;

// Battery Management State Machine
typedef enum {
    BATTERY_STATE_CRITICAL_LOW,     // < 3.7V, not charging -> deep sleep
    BATTERY_STATE_CHARGING,         // Any voltage, charging -> stay awake
    BATTERY_STATE_NORMAL_OPERATION, // 3.7V-4.1V, not charging -> light sleep after inactivity
    BATTERY_STATE_HIGH_BATTERY      // > 4.1V, any charging state -> stay awake
} battery_state_t;

// Battery flags and state
static volatile bool battery_low = false;
static volatile bool battery_charging = false;
static volatile bool battery_over_90 = false;
static battery_state_t current_battery_state = BATTERY_STATE_NORMAL_OPERATION;

// Accelerometer interrupt handling
static volatile bool int1_triggered = false;
static volatile uint32_t int1_count = 0;
static volatile int64_t last_shake_time = 0;

static bool sleep_mode_active = false;

// Battery readings
static float last_battery_voltage = 0.0;
static float last_battery_current = 0.0;

// Accelerometer streaming variables
static volatile uint32_t accel_sample_count = 0;

// LED charge status flag
static volatile bool override_charge_status_led = false;

// Audio state variables
static bool i2s_initialized = false;
static uint16_t last_led_data = 0;
static i2s_chan_handle_t tx_handle = NULL;
static int last_note_played = -1;
static bool in_rhythmic_phase = false;
static int locked_note_for_rhythm = -1;

// Server-controlled bleep parameters (set by RX task, read by audio task)
static volatile uint8_t bleep_note = 0;    // 0=off, 1=C, 2=F#, 3=D#
static volatile uint8_t bleep_period = 0;  // period in 50Hz frames (0=off)

// Mic state (server-triggered init via CMD_MIC_ON — NOT initialized at boot for OTA safety)
static i2s_chan_handle_t mic_rx_handle = NULL;
static volatile uint8_t mic_level = 0;     // 0-255 RMS amplitude
static bool mic_initialized = false;
static const float bleep_frequencies[] = {0.0f, 523.25f, 739.99f, 622.25f};

// Musical note frequencies (Hz)
#define NOTE_C 523.25
#define NOTE_CS 554.37
#define NOTE_D 587.33
#define NOTE_DS 622.25
#define NOTE_E 659.25
#define NOTE_F 698.46
#define NOTE_FS 739.99
#define NOTE_G 783.99
#define NOTE_GS 830.61
#define NOTE_A 880.00
#define NOTE_AS 932.33
#define NOTE_B 987.77

// Frequency lookup table matching icosahedron vertex order
static const float vertex_frequencies[12] = {
    NOTE_C,  NOTE_CS, NOTE_D,  NOTE_DS, NOTE_E,  NOTE_F,
    NOTE_FS, NOTE_G,  NOTE_GS, NOTE_A,  NOTE_AS, NOTE_B
};

// Icosahedron vertex colors (12 hues, 30° apart)
static const float icosa_colors[12][3] = {
    {1.0f, 0.0f, 0.0f},  {1.0f, 0.5f, 0.0f}, {1.0f, 1.0f, 0.0f},  {0.5f, 1.0f, 0.0f},
    {0.0f, 1.0f, 0.0f},  {0.0f, 1.0f, 0.5f}, {0.0f, 1.0f, 1.0f},  {0.0f, 0.5f, 1.0f},
    {0.0f, 0.0f, 1.0f},  {0.5f, 0.0f, 1.0f}, {1.0f, 0.0f, 1.0f},  {1.0f, 0.0f, 0.5f}
};

// ----- Audio helpers -----
static int get_vertex_from_rgb565(uint16_t rgb565) {
    float r = ((rgb565 >> 11) & 0x1F) / 31.0f;
    float g = ((rgb565 >> 5) & 0x3F) / 63.0f;
    float b = (rgb565 & 0x1F) / 31.0f;

    if (r < 0.1f && g < 0.1f && b < 0.1f) return -1;

    int best_vertex = 0;
    float min_distance = 999.0f;
    for (int i = 0; i < 12; i++) {
        float dr = r - icosa_colors[i][0];
        float dg = g - icosa_colors[i][1];
        float db = b - icosa_colors[i][2];
        float distance = dr*dr + dg*dg + db*db;
        if (distance < min_distance) {
            min_distance = distance;
            best_vertex = i;
        }
    }
    if (min_distance < 0.5f) return best_vertex;
    return -1;
}

// Icosahedron vertex 3D positions (LED i sits at vertex i)
static const float icosa_vertices[12][3] = {
    { 0.000f,  0.000f,  1.000f},  // 0: top
    { 0.724f,  0.526f,  0.447f},  // 1
    {-0.276f,  0.851f,  0.447f},  // 2
    {-0.894f,  0.000f,  0.447f},  // 3
    {-0.276f, -0.851f,  0.447f},  // 4
    { 0.724f, -0.526f,  0.447f},  // 5
    { 0.894f,  0.000f, -0.447f},  // 6
    { 0.276f,  0.851f, -0.447f},  // 7
    {-0.724f,  0.526f, -0.447f},  // 8
    {-0.724f, -0.526f, -0.447f},  // 9
    { 0.276f, -0.851f, -0.447f},  // 10
    { 0.000f,  0.000f, -1.000f},  // 11: bottom
};

// Self-LED mode: top LED shows gravity vertex color (server-triggered)
static bool self_led_enabled = false;

// Find which LED faces upward (closest to -gravity)
static int find_top_led(float ax, float ay, float az) {
    float norm = sqrtf(ax*ax + ay*ay + az*az);
    if (norm < 0.1f) return -1;
    float ux = -ax/norm, uy = -ay/norm, uz = -az/norm;
    int best = 0;
    float best_dot = ux * icosa_vertices[0][0] + uy * icosa_vertices[0][1] + uz * icosa_vertices[0][2];
    for (int i = 1; i < 12; i++) {
        float dot = ux * icosa_vertices[i][0] + uy * icosa_vertices[i][1] + uz * icosa_vertices[i][2];
        if (dot > best_dot) { best_dot = dot; best = i; }
    }
    return best;
}

static esp_err_t setup_amp(void) {
    if (i2s_initialized) {
        ESP_LOGI(AUDIO_TAG, "I2S already initialized");
        return ESP_OK;
    }
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 64;
    esp_err_t ret = i2s_new_channel(&chan_cfg, &tx_handle, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(AUDIO_TAG, "Failed to create I2S channel: %s", esp_err_to_name(ret));
        return ret;
    }
    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(AMP_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = AMP_BCLK,
            .ws   = AMP_LRCK,
            .dout = AMP_DIN,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    ret = i2s_channel_init_std_mode(tx_handle, &std_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(AUDIO_TAG, "Failed to init I2S std: %s", esp_err_to_name(ret));
        i2s_del_channel(tx_handle);
        return ret;
    }
    ret = i2s_channel_enable(tx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(AUDIO_TAG, "Failed to enable I2S channel: %s", esp_err_to_name(ret));
        i2s_del_channel(tx_handle);
        return ret;
    }
    i2s_initialized = true;
    ESP_LOGI(AUDIO_TAG, "I2S initialized successfully");
    return ESP_OK;
}

static void play_tone_frequency(float frequency, int duration_ms) {
    if (!i2s_initialized) {
        ESP_LOGW(AUDIO_TAG, "I2S not initialized - call setup_amp() first");
        return;
    }
    int16_t buffer[BUFFER_SIZE];
    float phase = 0.0f;
    const float phase_increment = 2 * PI * frequency / AMP_SAMPLE_RATE;
    int64_t start_time = esp_timer_get_time();
    int64_t duration_us = duration_ms * 1000;
    size_t bytes_written;

    ESP_LOGI(AUDIO_TAG, "Playing %.1f Hz tone for %d ms", frequency, duration_ms);
    while ((esp_timer_get_time() - start_time) < duration_us) {
        for (int i = 0; i < BUFFER_SIZE; i++) {
            buffer[i] = (int16_t)(sin(phase) * 12000);
            phase += phase_increment;
            if (phase >= 2 * PI) phase -= 2 * PI;
        }
        esp_err_t result = i2s_channel_write(tx_handle, buffer, sizeof(buffer), &bytes_written, pdMS_TO_TICKS(100));
        if (result != ESP_OK) {
            ESP_LOGE(AUDIO_TAG, "I2S write failed: %s", esp_err_to_name(result));
            break;
        }
    }
}

static void play_tone_frequency_with_envelope(float frequency, int duration_ms) {
    if (!i2s_initialized || tx_handle == NULL) {
        ESP_LOGW(AUDIO_TAG, "I2S not initialized");
        return;
    }
    int16_t buffer[BUFFER_SIZE];
    float phase = 0.0f;
    const float phase_increment = 2 * PI * frequency / AMP_SAMPLE_RATE;
    int64_t start_time = esp_timer_get_time();
    int64_t duration_us = duration_ms * 1000;
    size_t bytes_written;
    int total_samples = (duration_ms * AMP_SAMPLE_RATE) / 1000;
    int samples_played = 0;

    while ((esp_timer_get_time() - start_time) < duration_us) {
        for (int i = 0; i < BUFFER_SIZE; i++) {
            float envelope = 1.0f;
            float progress = (float)samples_played / (float)total_samples;
            if (progress < 0.06f) envelope = progress / 0.06f;        // fade-in ~5ms
            else if (progress > 0.875f) envelope = (1.0f - progress) / 0.125f; // fade-out ~10ms

            int16_t sample = (int16_t)(sin(phase) * 10000 * envelope);
            buffer[i] = sample;
            phase += phase_increment;
            if (phase >= 2 * PI) phase -= 2 * PI;
            samples_played++;
        }
        esp_err_t result = i2s_channel_write(tx_handle, buffer, sizeof(buffer), &bytes_written, pdMS_TO_TICKS(50));
        if (result != ESP_OK) break;
    }
}

static void handle_color_change(uint16_t new_led_data) {
    if (new_led_data == last_led_data) return;

    float r = ((new_led_data >> 11) & 0x1F) / 31.0f;
    float g = ((new_led_data >> 5) & 0x3F) / 63.0f;
    float b = ( new_led_data       & 0x1F) / 31.0f;

    if (r < 0.05f && g < 0.05f && b < 0.05f) {
        last_note_played = -1;
        last_led_data = new_led_data;
        return;
    }

    float cmax = fmaxf(r, fmaxf(g, b));
    float cmin = fminf(r, fminf(g, b));
    float delta = cmax - cmin;
    float hue = 0.0f;

    if (delta > 1e-6f) {
        if (cmax == r)      hue = 60.0f * fmodf(((g - b) / delta), 6.0f);
        else if (cmax == g) hue = 60.0f * (((b - r) / delta) + 2.0f);
        else                hue = 60.0f * (((r - g) / delta) + 4.0f);
        if (hue < 0.0f) hue += 360.0f;
    } else {
        last_note_played = -1;
        last_led_data = new_led_data;
        return;
    }

    int note_index = ((int)((hue + 15.0f) / 30.0f)) % 12;
    if (note_index != last_note_played) {
        float frequency = vertex_frequencies[note_index];
        ESP_LOGI(AUDIO_TAG, "Hue=%.1f° -> note %d (%.1f Hz)", hue, note_index, frequency);
        // play_tone_frequency_with_envelope(frequency, TONE_DURATION_MS);
        last_note_played = note_index;
    }
    last_led_data = new_led_data;
}

// Non-blocking short bleep (~30ms). Safe on core 0 — well under 5s TWDT.
static void play_short_bleep(float frequency) {
    if (!i2s_initialized || tx_handle == NULL) return;

    int16_t buffer[64];
    float phase = 0.0f;
    const float phase_inc = 2.0f * PI * frequency / AMP_SAMPLE_RATE;
    const int total_samples = (30 * AMP_SAMPLE_RATE) / 1000; // 30ms
    int samples_played = 0;
    size_t bytes_written;

    while (samples_played < total_samples) {
        int chunk = total_samples - samples_played;
        if (chunk > 64) chunk = 64;

        for (int i = 0; i < chunk; i++) {
            float progress = (float)samples_played / (float)total_samples;
            float envelope = 1.0f;
            if (progress < 0.1f) envelope = progress / 0.1f;           // 3ms attack
            else if (progress > 0.7f) envelope = (1.0f - progress) / 0.3f; // 9ms release

            buffer[i] = (int16_t)(sinf(phase) * 32000.0f * envelope);
            phase += phase_inc;
            if (phase >= 2.0f * PI) phase -= 2.0f * PI;
            samples_played++;
        }

        i2s_channel_write(tx_handle, buffer, chunk * sizeof(int16_t), &bytes_written, pdMS_TO_TICKS(50));
    }
}

// Audio bleep task: runs on core 0 at low priority.
// Reads server-controlled bleep_note and bleep_period globals.
// Bleep period is in 50Hz frames; we check every 20ms (one frame).
static void audio_bleep_task(void *arg) {
    if (setup_amp() != ESP_OK) {
        ESP_LOGE(AUDIO_TAG, "Failed to setup audio - audio task exiting");
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(AUDIO_TAG, "Audio bleep task running");

    int frame_counter = 0;

    while (1) {
        uint8_t note = bleep_note;
        uint8_t period = bleep_period;

        if (note > 0 && note <= 3 && period > 0) {
            frame_counter++;
            if (frame_counter >= period) {
                frame_counter = 0;
                play_short_bleep(bleep_frequencies[note]);
            }
        } else {
            frame_counter = 0;
        }

        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

// ----- Microphone (server-triggered, NOT called at boot) -----
static esp_err_t setup_mic(void) {
    if (mic_initialized) return ESP_OK;

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 64;

    esp_err_t ret = i2s_new_channel(&chan_cfg, NULL, &mic_rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(MIC_TAG, "Failed to create mic channel: %s", esp_err_to_name(ret));
        return ret;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(MIC_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = MIC_SCK,
            .ws   = MIC_WS,
            .dout = I2S_GPIO_UNUSED,
            .din  = MIC_SD,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };

    ret = i2s_channel_init_std_mode(mic_rx_handle, &std_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(MIC_TAG, "Failed to init mic I2S: %s", esp_err_to_name(ret));
        i2s_del_channel(mic_rx_handle);
        mic_rx_handle = NULL;
        return ret;
    }

    ret = i2s_channel_enable(mic_rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(MIC_TAG, "Failed to enable mic channel: %s", esp_err_to_name(ret));
        i2s_del_channel(mic_rx_handle);
        mic_rx_handle = NULL;
        return ret;
    }

    mic_initialized = true;
    ESP_LOGI(MIC_TAG, "Microphone initialized on I2S1");
    return ESP_OK;
}

// Mic RMS task: core 0, priority 1. Reads audio, computes amplitude.
static void mic_rms_task(void *arg) {
    int32_t buf[64];
    size_t bytes_read;
    float smoothed_rms = 0.0f;

    ESP_LOGI(MIC_TAG, "Mic RMS task running");

    while (1) {
        esp_err_t ret = i2s_channel_read(mic_rx_handle, buf, sizeof(buf), &bytes_read, pdMS_TO_TICKS(50));
        if (ret != ESP_OK || bytes_read == 0) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        int num_samples = bytes_read / sizeof(int32_t);
        // Compute RMS (ICS-43434 outputs 24-bit left-justified in 32-bit)
        float sum_sq = 0.0f;
        for (int i = 0; i < num_samples; i++) {
            float sample = (float)(buf[i] >> 8) / 8388608.0f; // normalize 24-bit to -1..1
            sum_sq += sample * sample;
        }
        float rms = sqrtf(sum_sq / num_samples);

        // EMA smoothing
        smoothed_rms = 0.85f * smoothed_rms + 0.15f * rms;

        // Scale to 0-255 (rms typically 0-0.5 for normal ambient, clap peaks near 1.0)
        int level = (int)(smoothed_rms * 512.0f);
        if (level > 255) level = 255;
        mic_level = (uint8_t)level;

        vTaskDelay(1); // yield for core 0 TWDT
    }
}

// ----- Accelerometer + wake handling -----
static void IRAM_ATTR lsm6ds_int1_handler(void *arg) {
    int1_triggered = true;
    int1_count++;
    last_shake_time = esp_timer_get_time();
}

static esp_err_t lsm6ds_write_reg(uint8_t reg, uint8_t data) {
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (0x6A << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_write_byte(cmd, data, true);
    i2c_master_stop(cmd);
    esp_err_t ret = i2c_master_cmd_begin(I2C_NUM_0, cmd, pdMS_TO_TICKS(1000));
    i2c_cmd_link_delete(cmd);
    return ret;
}

static esp_err_t lsm6ds_read_reg(uint8_t reg, uint8_t *data) {
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (0x6A << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (0x6A << 1) | I2C_MASTER_READ, true);
    i2c_master_read_byte(cmd, data, I2C_MASTER_NACK);
    i2c_master_stop(cmd);
    esp_err_t ret = i2c_master_cmd_begin(I2C_NUM_0, cmd, pdMS_TO_TICKS(1000));
    i2c_cmd_link_delete(cmd);
    return ret;
}

static bool check_lsm6ds_wakeup(void) {
    uint8_t wake_up_src;
    if (lsm6ds_read_reg(0x1B, &wake_up_src) == ESP_OK) {
        return (wake_up_src & 0x08) != 0; // WU_IA bit
    }
    return false;
}

static void check_wakeup_reason(void) {
    esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
    switch(wakeup_reason) {
        case ESP_SLEEP_WAKEUP_EXT0:
            ESP_LOGI(SLEEP_TAG, "Woke up from accelerometer shake!");
            last_shake_time = esp_timer_get_time();
            break;
        case ESP_SLEEP_WAKEUP_TIMER:
            ESP_LOGI(SLEEP_TAG, "Woke up from timer - checking charging status");
            break;
        case ESP_SLEEP_WAKEUP_UNDEFINED:
        default:
            ESP_LOGI(SLEEP_TAG, "Normal startup (power on)");
            last_shake_time = esp_timer_get_time();
            break;
    }
}

static void check_lsm6ds_interrupts(void) {
    if (int1_triggered) {
        int1_triggered = false;
        ESP_LOGI(SLEEP_TAG, "INT1 triggered! Count: %lu", int1_count);
        if (check_lsm6ds_wakeup()) {
            ESP_LOGI(SLEEP_TAG, "Wake-up event detected!");
        }
        last_shake_time = esp_timer_get_time();
        ESP_LOGI(SLEEP_TAG, "Shake timer reset due to interrupt");
    }
}

static esp_err_t read_accelerometer_data(float *ax, float *ay, float *az) {
    uint8_t accel_data[6];
    esp_err_t ret = ESP_OK;
    for (int i = 0; i < 6; i++) ret |= lsm6ds_read_reg(0x28 + i, &accel_data[i]);
    if (ret == ESP_OK) {
        int16_t raw_x = (int16_t)((accel_data[1] << 8) | accel_data[0]);
        int16_t raw_y = (int16_t)((accel_data[3] << 8) | accel_data[2]);
        int16_t raw_z = (int16_t)((accel_data[5] << 8) | accel_data[4]);
        *ax = raw_x / 16384.0f;
        *ay = raw_y / 16384.0f;
        *az = raw_z / 16384.0f;
        return ESP_OK;
    } else {
        *ax = 0.0f; *ay = 0.0f; *az = -1.0f;
        return ESP_FAIL;
    }
}

static esp_err_t read_gyroscope_data(float *gx, float *gy, float *gz) {
    uint8_t gyro_data[6];
    esp_err_t ret = ESP_OK;
    for (int i = 0; i < 6; i++) ret |= lsm6ds_read_reg(0x22 + i, &gyro_data[i]);
    if (ret == ESP_OK) {
        int16_t raw_x = (int16_t)((gyro_data[1] << 8) | gyro_data[0]);
        int16_t raw_y = (int16_t)((gyro_data[3] << 8) | gyro_data[2]);
        int16_t raw_z = (int16_t)((gyro_data[5] << 8) | gyro_data[4]);
        // 17.50 mdps/LSB at +/-500 dps
        *gx = raw_x * 0.01750f;
        *gy = raw_y * 0.01750f;
        *gz = raw_z * 0.01750f;
        return ESP_OK;
    } else {
        *gx = 0.0f; *gy = 0.0f; *gz = 0.0f;
        return ESP_FAIL;
    }
}

// ----- Battery color + APA102 -----
static rgb_color_t get_battery_color(float voltage) {
    rgb_color_t color;
    if (voltage < BATTERY_MIN) {
        color.r = 255; color.g = 0; color.b = 0; // Red
    } else if (voltage >= BATTERY_MAX) {
        color.r = 0; color.g = 0; color.b = 255; // Blue
    } else if (voltage <= BATTERY_MID) {
        uint8_t g = (uint8_t)((voltage - BATTERY_MIN) / (BATTERY_MID - BATTERY_MIN) * 255);
        uint8_t r = 255 - g;
        color.r = r; color.g = g; color.b = 0;
    } else {
        uint8_t b = (uint8_t)((voltage - BATTERY_MID) / (BATTERY_MAX - BATTERY_MID) * 255);
        uint8_t g = 255 - b;
        color.r = 0; color.g = g; color.b = b;
    }
    return color;
}

static void apa102_init(void) {
    ESP_LOGI(PIXEL_TAG, "Initializing APA102 on pins CLK=%d, DATA=%d", CLK_PIN, DATA_PIN);
    spi_bus_config_t bus_config = {
        .mosi_io_num = DATA_PIN,
        .miso_io_num = -1,
        .sclk_io_num = CLK_PIN,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = (4 + NUM_LEDS * 4 + 4) * 8
    };
    spi_device_interface_config_t dev_config = {
        .clock_speed_hz = 1000000,
        .mode = 0,
        .spics_io_num = -1,
        .queue_size = 1,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus_config, SPI_DMA_CH_AUTO));
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev_config, &spi_device));
    memset(leds, 0, sizeof(leds));
    ESP_LOGI(PIXEL_TAG, "APA102 initialized successfully");
}

static void apa102_show(void) {
    uint8_t tx_data[4 + NUM_LEDS * 4 + 4];
    int idx = 0;
    tx_data[idx++] = 0x00; tx_data[idx++] = 0x00; tx_data[idx++] = 0x00; tx_data[idx++] = 0x00;
    for (int i = 0; i < NUM_LEDS; i++) {
        tx_data[idx++] = 0xE0 | (APA102_BRIGHTNESS >> 3);
        tx_data[idx++] = leds[i].b;
        tx_data[idx++] = leds[i].g;
        tx_data[idx++] = leds[i].r;
    }
    tx_data[idx++] = 0xFF; tx_data[idx++] = 0xFF; tx_data[idx++] = 0xFF; tx_data[idx++] = 0xFF;
    spi_transaction_t trans = { .length = idx * 8, .tx_buffer = tx_data, .rx_buffer = NULL };
    ESP_ERROR_CHECK(spi_device_transmit(spi_device, &trans));
}

static void apa102_fill_solid(rgb_color_t color) {
    for (int i = 0; i < NUM_LEDS; i++) leds[i] = color;
}

static void apa102_clear(void) {
    rgb_color_t black = {0, 0, 0};
    apa102_fill_solid(black);
}

// ----- Battery state -----
static void update_battery_flags(float voltage, float current_ma) {
    battery_low = (voltage < BATTERY_MIN);
    battery_over_90 = (voltage > BATTERY_90_PERCENT);
    if (current_ma > CHARGING_THRESHOLD_MA) battery_charging = true;
    else if (current_ma < NOT_CHARGING_THRESHOLD_MA) battery_charging = false;
}

static void update_battery_state(void) {
    battery_state_t new_state;
    if (battery_low && !battery_charging) new_state = BATTERY_STATE_CRITICAL_LOW;
    else if (battery_charging)            new_state = BATTERY_STATE_CHARGING;
    // else if (battery_over_90)             new_state = BATTERY_STATE_HIGH_BATTERY;
    else                                  new_state = BATTERY_STATE_NORMAL_OPERATION;

    if (new_state != current_battery_state) {
        const char* names[] = { "CRITICAL_LOW (deep sleep)", "CHARGING (stay awake)", "NORMAL_OPERATION (light sleep on inactivity)", "HIGH_BATTERY (stay awake)" };
        ESP_LOGI(BATTERY_TAG, "State change: %s -> %s", names[current_battery_state], names[new_state]);
        ESP_LOGI(BATTERY_TAG, " Voltage: %.2fV, Current: %.1fmA", last_battery_voltage, last_battery_current);
        ESP_LOGI(BATTERY_TAG, " Flags - Low:%d, Charging:%d, >90%%:%d", battery_low, battery_charging, battery_over_90);
        current_battery_state = new_state;
    }
}

static bool should_enter_sleep(void) {
    int64_t current_time = esp_timer_get_time();
    int64_t time_since_shake = current_time - last_shake_time;

    switch (current_battery_state) {
        case BATTERY_STATE_CRITICAL_LOW: return true;
        case BATTERY_STATE_CHARGING:
        case BATTERY_STATE_HIGH_BATTERY: return false;
        case BATTERY_STATE_NORMAL_OPERATION: return (time_since_shake > (SLEEP_TIMEOUT_MS * 1000));
        default: return false;
    }
}

static void enter_sleep_mode(void) {
    ESP_LOGI(SLEEP_TAG, "Entering sleep mode, state: %d", current_battery_state);
    apa102_clear();
    apa102_show();

    switch (current_battery_state) {
        case BATTERY_STATE_CRITICAL_LOW:
            ESP_LOGI(SLEEP_TAG, "Critical low battery - deep sleep for 5 seconds");
            esp_sleep_enable_timer_wakeup(DEEP_SLEEP_DURATION_US);
            esp_sleep_enable_ext0_wakeup(LSM_INT_1, 1);
            esp_deep_sleep_start();
            break;
        case BATTERY_STATE_NORMAL_OPERATION:
            ESP_LOGI(SLEEP_TAG, "Normal operation - light sleep until shake");
            esp_sleep_enable_ext0_wakeup(LSM_INT_1, 1);
            ESP_LOGI(SLEEP_TAG, "Wake-up source configured for GPIO %d", LSM_INT_1);
            esp_light_sleep_start();
            ESP_LOGI(SLEEP_TAG, "Woke from light sleep");
            {
                esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
                if (cause == ESP_SLEEP_WAKEUP_EXT0) ESP_LOGI(SLEEP_TAG, "Woke from GPIO interrupt (shake detected)");
                else ESP_LOGI(SLEEP_TAG, "Woke from other cause: %d", cause);
            }
            last_shake_time = esp_timer_get_time();
            break;
        default:
            ESP_LOGW(SLEEP_TAG, "Unexpected sleep request in state %d", current_battery_state);
            break;
    }
}

// Configure LSM6DS for wake detection (as per your compact config)
static esp_err_t configure_lsm6ds(void) {
    ESP_LOGI(ACCEL_TAG, "Configuring LSM6DS for wake-up detection");
    uint8_t who_am_i;
    if (lsm6ds_read_reg(0x0F, &who_am_i) != ESP_OK) {
        ESP_LOGE(ACCEL_TAG, "Failed to read WHO_AM_I");
        return ESP_FAIL;
    }
    ESP_LOGI(ACCEL_TAG, "WHO_AM_I: 0x%02X", who_am_i);

    if (0)
    {
        lsm6ds_write_reg(0x10, 0x20); // 26Hz, ±2g
        lsm6ds_write_reg(0x11, 0x00); // Gyro off
        lsm6ds_write_reg(0x12, 0x00); // Active high, push-pull
        lsm6ds_write_reg(0x58, 0x90); // Timer + slope
        lsm6ds_write_reg(0x5B, 0x0F); // ~15 mg threshold
        lsm6ds_write_reg(0x5C, 0x20); // Duration = 1
        lsm6ds_write_reg(0x0D, 0x20); // INT1 wake
        lsm6ds_write_reg(0x5E, 0x20); // MD1_CFG route wake to INT1
    } else {
        lsm6ds_write_reg(0x10, 0x72); // 833Hz, +-2g, LPF1_BW_SEL
        lsm6ds_write_reg(0x17, 0x80); // enable LPF2, ODR50 cutoff
        lsm6ds_write_reg(0x15, 0x00); // high performance mode
        lsm6ds_write_reg(0x11, 0x72); // 833Hz, +/-500 dps gyro
        lsm6ds_write_reg(0x12, 0x00); // Active high, push-pull
        lsm6ds_write_reg(0x58, 0x90); // Timer + slope
        lsm6ds_write_reg(0x5B, 0x0F); // ~15 mg threshold
        lsm6ds_write_reg(0x5C, 0x20); // Duration = 1
        lsm6ds_write_reg(0x0D, 0x20); // INT1 wake
        lsm6ds_write_reg(0x5E, 0x20); // MD1_CFG route wake to INT1
    }
    uint8_t dummy; lsm6ds_read_reg(0x1B, &dummy);
    ESP_LOGI(ACCEL_TAG, "LSM6DS configured: wake ~15mg, 833Hz, gyro 500dps");
    return ESP_OK;
}

static esp_err_t setup_lsm6ds_interrupt(void) {
    ESP_LOGI(ACCEL_TAG, "Setting up LSM6DS interrupt on GPIO %d", LSM_INT_1);
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << LSM_INT_1),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));
    ESP_ERROR_CHECK(esp_sleep_enable_ext0_wakeup(LSM_INT_1, 1));

    esp_err_t ret = gpio_install_isr_service(0);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(ACCEL_TAG, "Failed to install GPIO ISR service: %s", esp_err_to_name(ret));
        return ret;
    }
    ESP_ERROR_CHECK(gpio_isr_handler_add(LSM_INT_1, lsm6ds_int1_handler, NULL));
    ESP_ERROR_CHECK(gpio_intr_enable(LSM_INT_1));
    ESP_ERROR_CHECK(gpio_set_intr_type(LSM_INT_1, GPIO_INTR_POSEDGE));

    esp_err_t lsm_ret = configure_lsm6ds();
    if (lsm_ret != ESP_OK) {
        ESP_LOGE(ACCEL_TAG, "Failed to configure LSM6DS");
        return lsm_ret;
    }
    last_shake_time = esp_timer_get_time();
    ESP_LOGI(ACCEL_TAG, "LSM6DS interrupt configured successfully");
    return ESP_OK;
}

// ===================== ESP-NOW Proximity =====================

static void espnow_recv_cb(const esp_now_recv_info_t *recv_info, const uint8_t *data, int data_len) {
    if (data_len < 4 || !recv_info->rx_ctrl) return;

    uint8_t peer_slot = data[0];
    if (peer_slot >= MAX_ORBS) return;

    uint32_t peer_serial = ((uint32_t)data[1] << 16) | ((uint32_t)data[2] << 8) | data[3];
    maybe_resolve_slot_collision(peer_slot, peer_serial);

    int8_t rssi = recv_info->rx_ctrl->rssi;

    if (xSemaphoreTake(rssi_mutex, pdMS_TO_TICKS(5)) == pdTRUE) {
        rssi_matrix[peer_slot] = (int8_t)(0.7f * rssi_matrix[peer_slot] + 0.3f * rssi);
        int64_t now = esp_timer_get_time();
        rssi_last_seen[peer_slot] = now;
        peer_matrix_last_seen[peer_slot] = now;

        // New 17-byte frame carries the sender's top-6 view so every orb can assemble
        // a full NxN mutual-strength matrix and run study-branch clustering locally.
        // Older 8-byte frames are still accepted; we just don't learn the sender's view.
        if (data_len >= ESPNOW_FRAME_SIZE) {
            uint8_t nn = data[4];
            if (nn > 6) nn = 6;
            for (int i = 0; i < MAX_ORBS; i++) peer_matrix[peer_slot][i] = 0;
            for (int i = 0; i < nn; i++) {
                uint8_t ps = data[5 + i*2];
                uint8_t st = data[5 + i*2 + 1];
                if (ps < MAX_ORBS && st > 0) {
                    peer_matrix[peer_slot][ps] = st;
                }
            }
        }
        xSemaphoreGive(rssi_mutex);
    }

    static int rx_count = 0;
    if (++rx_count % 50 == 0) {
        ESP_LOGI(ESPNOW_TAG, "RX #%d from slot=%d rssi=%d len=%d", rx_count, peer_slot, rssi, data_len);
    }
}

static void espnow_timer_cb(void *arg) {
    if (espnow_send_sem) xSemaphoreGive(espnow_send_sem);
}

// Periodic pacer: fires only while the server is silent, so proximity broadcasts
// keep flowing in standalone mode.
static void standalone_espnow_timer_cb(void *arg) {
    if (online_watchdog > ONLINE_WATCHDOG_SILENT_THRESHOLD &&
        !ota_in_progress &&
        espnow_send_sem) {
        xSemaphoreGive(espnow_send_sem);
    }
}

static void espnow_send_cb(const uint8_t *mac_addr, esp_now_send_status_t status) {
    if (status != ESP_NOW_SEND_SUCCESS) {
        ESP_LOGD(ESPNOW_TAG, "ESP-NOW send failed");
    }
}

static esp_err_t espnow_init(void) {
    if (espnow_initialized) return ESP_OK;

    rssi_mutex = xSemaphoreCreateMutex();
    if (!rssi_mutex) return ESP_ERR_NO_MEM;
    memset(rssi_matrix, -127, sizeof(rssi_matrix));
    memset(rssi_last_seen, 0, sizeof(rssi_last_seen));

    esp_err_t ret = esp_now_init();
    if (ret != ESP_OK) {
        ESP_LOGE(ESPNOW_TAG, "esp_now_init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    esp_now_register_recv_cb(espnow_recv_cb);
    esp_now_register_send_cb(espnow_send_cb);

    esp_now_peer_info_t peer = {0};
    memcpy(peer.peer_addr, espnow_broadcast_addr, 6);
    peer.channel = 0;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;

    ret = esp_now_add_peer(&peer);
    if (ret != ESP_OK) {
        ESP_LOGE(ESPNOW_TAG, "Failed to add broadcast peer: %s", esp_err_to_name(ret));
        esp_now_deinit();
        return ret;
    }

    espnow_initialized = true;
    ESP_LOGI(ESPNOW_TAG, "ESP-NOW initialized, broadcast peer added");
    return ESP_OK;
}

static void espnow_deinit_safe(void) {
    if (!espnow_initialized) return;
    esp_now_deinit();
    espnow_initialized = false;
    ESP_LOGI(ESPNOW_TAG, "ESP-NOW deinitialized");
}

static void compute_topN_strengths(int exclude_slot, int n, uint8_t *out_slots, uint8_t *out_strengths);

static void espnow_broadcast_task(void *arg) {
    uint8_t frame[ESPNOW_FRAME_SIZE] = {0};
    ESP_LOGI(ESPNOW_TAG, "ESP-NOW broadcast task started");

    while (1) {
        if (xSemaphoreTake(espnow_send_sem, pdMS_TO_TICKS(200)) != pdTRUE) {
            continue;
        }
        bool standalone_active = online_watchdog > ONLINE_WATCHDOG_SILENT_THRESHOLD;
        if (ota_in_progress || !espnow_initialized) {
            continue;
        }
        // Normally we broadcast only when the server has enabled proximity and given us a slot.
        // In standalone mode we broadcast regardless so peers can still find us; if we don't yet
        // have a server-assigned slot, self-assign a 5-bit id derived from the serial. Collisions
        // at this rate are acceptable and evaporate the moment the server resumes and reallocates.
        int use_slot = (slot_alloc >= 0 && slot_alloc < MAX_ORBS) ? slot_alloc : self_slot_effective();
        if (!standalone_active && !proximity_enabled) continue;
        if (!standalone_active && (slot_alloc < 0 || slot_alloc >= MAX_ORBS)) continue;

        // Frame: [slot(1) | serial(3) | num_peers(1) | {slot,strength}x6 (12)] = 17B
        uint8_t top_slots[6], top_strengths[6];
        compute_topN_strengths(use_slot, 6, top_slots, top_strengths);  // inter-orb frame stays 6
        frame[0] = (uint8_t)use_slot;
        frame[1] = (serial >> 16) & 0xFF;
        frame[2] = (serial >> 8) & 0xFF;
        frame[3] = serial & 0xFF;
        frame[4] = 6;  // count; unused entries carry strength=0
        for (int i = 0; i < 6; i++) {
            frame[5 + i*2]     = top_slots[i];
            frame[5 + i*2 + 1] = top_strengths[i];
        }

        esp_err_t err = esp_now_send(espnow_broadcast_addr, frame, sizeof(frame));
        static int send_count = 0;
        if (++send_count % 50 == 0) {
            uint8_t ch = 0;
            esp_wifi_get_channel(&ch, NULL);
            ESP_LOGI(ESPNOW_TAG, "TX #%d slot=%d err=%d ch=%d", send_count, frame[0], err, ch);
        }
    }
}

// Shared by the server-facing unicast and the ESP-NOW broadcast: pick the six
// strongest non-stale peers and convert their dBm RSSI into 0..255 strength.
// Output slots[i]=0 + strengths[i]=0 means "empty slot" (fewer than 6 peers seen).
// `exclude_slot` < 0 skips the self-exclusion.
static void compute_topN_strengths(int exclude_slot, int n, uint8_t *out_slots, uint8_t *out_strengths) {
    if (n > MAX_ORBS) n = MAX_ORBS;
    typedef struct { int slot; int8_t rssi; } rssi_peer_t;
    rssi_peer_t top[MAX_ORBS];
    for (int i = 0; i < n; i++) { top[i].slot = 0; top[i].rssi = -127; }

    if (xSemaphoreTake(rssi_mutex, pdMS_TO_TICKS(5)) == pdTRUE) {
        int64_t now = esp_timer_get_time();
        for (int orb = 0; orb < MAX_ORBS; orb++) {
            if (rssi_matrix[orb] > -127 && (now - rssi_last_seen[orb]) > RSSI_STALE_US) {
                rssi_matrix[orb] = -127;
            }
        }
        for (int orb = 0; orb < MAX_ORBS; orb++) {
            if (orb == exclude_slot) continue;
            int8_t rssi = rssi_matrix[orb];
            if (rssi <= -127) continue;
            int min_idx = 0;
            for (int j = 1; j < n; j++) {
                if (top[j].rssi < top[min_idx].rssi) min_idx = j;
            }
            if (rssi > top[min_idx].rssi) {
                top[min_idx].slot = orb;
                top[min_idx].rssi = rssi;
            }
        }
        xSemaphoreGive(rssi_mutex);
    }

    for (int i = 0; i < n; i++) {
        out_slots[i] = (uint8_t)top[i].slot;
        if (top[i].rssi > -127) {
            // Saturating map (3.23 fleet baseline): *3 pins links closer than
            // ~-15 dBm at 255. The de-saturated *255/100 variant (v3.24, commit
            // c0b0858) restored the within-cluster peak but made clusters harder
            // to achieve on the bench — parked pending a deliberate A/B.
            int val = ((int)top[i].rssi + 100) * 3;
            if (val < 0) val = 0;
            if (val > 255) val = 255;
            out_strengths[i] = (uint8_t)val;
        } else {
            out_strengths[i] = 0;
        }
    }
}

// Pack the top-PROX_N strongest RSSI peers into r_packet.rssi_proximity for upload.
static void pack_rssi_proximity(void) {
    uint8_t slots[PROX_N], strengths[PROX_N];
    compute_topN_strengths(slot_alloc, PROX_N, slots, strengths);
    for (int i = 0; i < PROX_N; i++) {
        r_packet.rssi_proximity[i].slot_id = slots[i];
        r_packet.rssi_proximity[i].strength = strengths[i];
    }
}

// ===================== End ESP-NOW =====================

// ----- Battery management task -----
static void battery_management_task(void *arg) {
    ina219_data_t data;
    static uint8_t flash_counter = 0;
    while (1) {
        check_lsm6ds_interrupts();

        if (ina219_read(&data) == ESP_OK) {
            last_battery_voltage = data.bus_voltage;
            last_battery_current = data.current_mA;

            update_battery_flags(last_battery_voltage, last_battery_current);
            update_battery_state();

            if (ota_in_progress) {
                // OTA owns LED; do nothing here
            } else if (battery_charging && !override_charge_status_led) {
                rgb_color_t battery_color = get_battery_color(last_battery_voltage);
                rgb_color_t black = {0, 0, 0};
                apa102_fill_solid(black);
                flash_counter++;
                if (flash_counter >= 10) {
                    flash_counter = 0;
                    leds[0] = battery_color;
                }
                apa102_show();
            } else {
                // not charging: let normal LED/path handle it
            }
        } else {
            ESP_LOGE(BATTERY_TAG, "Failed to read INA219");
        }

        if (should_enter_sleep()) {
            sleep_mode_active = true;
            enter_sleep_mode();
            sleep_mode_active = false;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

// ----- OTA HTTP event handler -----
static esp_err_t _http_event_handler(esp_http_client_event_t *evt) {
    switch (evt->event_id) {
        case HTTP_EVENT_ERROR:         ESP_LOGD(OTA_TAG, "HTTP_EVENT_ERROR"); break;
        case HTTP_EVENT_ON_CONNECTED:  ESP_LOGD(OTA_TAG, "HTTP_EVENT_ON_CONNECTED"); break;
        case HTTP_EVENT_HEADER_SENT:   ESP_LOGD(OTA_TAG, "HTTP_EVENT_HEADER_SENT"); break;
        case HTTP_EVENT_ON_HEADER:     ESP_LOGD(OTA_TAG, "HTTP_EVENT_ON_HEADER, key=%s, value=%s", evt->header_key, evt->header_value); break;
        case HTTP_EVENT_ON_DATA:       ESP_LOGD(OTA_TAG, "HTTP_EVENT_ON_DATA, len=%d", evt->data_len); break;
        case HTTP_EVENT_ON_FINISH:     ESP_LOGD(OTA_TAG, "HTTP_EVENT_ON_FINISH"); break;
        case HTTP_EVENT_DISCONNECTED:  ESP_LOGD(OTA_TAG, "HTTP_EVENT_DISCONNECTED"); break;
        case HTTP_EVENT_REDIRECT:      ESP_LOGD(OTA_TAG, "HTTP_EVENT_REDIRECT"); break;
        default: break;
    }
    return ESP_OK;
}

// ----- OTA helpers implementation -----
static void ota_quiet_begin(void) {
    ota_in_progress = true;
    override_charge_status_led = true;  // prevent charging blink overriding status
    apa102_fill_solid(color_ota());
    apa102_show();
}

static void ota_quiet_end(void) {
    override_charge_status_led = false;
    apa102_fill_solid(color_black());
    apa102_show();
    ota_in_progress = false;
}

// OTA update task (resilient, retries, always reboots to recover)
void ota_update_task(void *pvParameter) {
    ota_quiet_begin();
    ESP_LOGI(OTA_TAG, "Starting OTA update...");

    const int max_attempts = 2;
    esp_err_t ret = ESP_FAIL;

    for (int attempt = 1; attempt <= max_attempts; ++attempt) {
        ESP_LOGI(OTA_TAG, "OTA attempt %d/%d", attempt, max_attempts);
        esp_http_client_config_t config = {
            .url = OTA_URL,
            .event_handler = _http_event_handler,
            .keep_alive_enable = true,
            .skip_cert_common_name_check = true,
            .disable_auto_redirect = false,
            .timeout_ms = 45000,
        };
        esp_https_ota_config_t ota_config = {
            .http_config = &config,
            .http_client_init_cb = NULL,
            .bulk_flash_erase = false,
            .partial_http_download = false,
        };
        ESP_LOGI(OTA_TAG, "Downloading update from %s", config.url);
        ret = esp_https_ota(&ota_config);
        if (ret == ESP_OK) break;
        ESP_LOGE(OTA_TAG, "OTA attempt %d failed: %s", attempt, esp_err_to_name(ret));
        vTaskDelay(pdMS_TO_TICKS(1500));
    }

    if (ret == ESP_OK) {
        ESP_LOGI(OTA_TAG, "OTA succeeded — rebooting into new firmware");
        vTaskDelay(pdMS_TO_TICKS(200));
        esp_restart(); // never returns
    } else {
        ESP_LOGE(OTA_TAG, "OTA failed after retries: %s — rebooting to recover", esp_err_to_name(ret));
        vTaskDelay(pdMS_TO_TICKS(500));
        ota_quiet_end(); // tidy (not required before restart)
        esp_restart();   // return to a clean state with RX/TX live
    }

    // Not reached; safety:
    ota_quiet_end();
    ota_task_handle = NULL;
    vTaskDelete(NULL);
}

void trigger_ota_update(void) {
    if (ota_in_progress) {
        ESP_LOGW(OTA_TAG, "OTA already in progress; ignoring trigger");
        return;
    }
    if (ota_task_handle != NULL) {
        ESP_LOGW(OTA_TAG, "OTA task handle already set; ignoring trigger");
        return;
    }
    ESP_LOGI(OTA_TAG, "OTA update triggered!");
    BaseType_t ok = xTaskCreatePinnedToCore(
        &ota_update_task, "ota_update_task", 8192, NULL, 6, &ota_task_handle, 1);
    if (ok != pdPASS) {
        ESP_LOGE(OTA_TAG, "Failed to create OTA task");
        ota_task_handle = NULL;
    }
}

// ----- Timer ISR -----
static bool IRAM_ATTR on_alarm_cb(gptimer_handle_t timer, const gptimer_alarm_event_data_t *edata, void *user_ctx) {
    BaseType_t higher = pdFALSE;
    xSemaphoreGiveFromISR(send_semaphore, &higher);
    return (higher == pdTRUE);
}

// ----- Wi-Fi/Event handling -----
// Event handler stays lightweight: signal bits, let wifi_reconnect_task handle retries.
static void event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = (wifi_event_sta_disconnected_t *)event_data;
        ESP_LOGI(TAG, "Disconnected, reason=%d", d ? d->reason : -1);
        xEventGroupClearBits(wifi_event_group, WIFI_CONNECTED_BIT);
        xEventGroupSetBits(wifi_event_group, WIFI_DISCONNECTED_BIT);
        if (ota_in_progress) {
            ESP_LOGW(OTA_TAG, "Wi-Fi dropped during OTA; reconnect pending");
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ESP_LOGI(TAG, "Got IP");
        wifi_retry_count = 0;
        wifi_led_state = WIFI_LED_OFF;
        xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

// Backoff reconnect with periodic full supplicant reset.
// Fixes boot-order bug where orb powered up before AP is reachable
// thrashed the driver into a stuck state.
static void wifi_reconnect_task(void *arg) {
    while (1) {
        xEventGroupWaitBits(wifi_event_group, WIFI_DISCONNECTED_BIT, pdTRUE, pdFALSE, portMAX_DELAY);
        if (xEventGroupGetBits(wifi_event_group) & WIFI_CONNECTED_BIT) continue;

        int backoff_idx = wifi_retry_count < 4 ? wifi_retry_count : 4;
        int backoff_ms = 500 << backoff_idx;
        wifi_retry_count++;

        wifi_led_state = (wifi_retry_count >= WIFI_STUCK_LED_THRESHOLD)
            ? WIFI_LED_STUCK : WIFI_LED_SEARCHING;

        ESP_LOGI(TAG, "WiFi reconnect attempt %d, backoff %dms", wifi_retry_count, backoff_ms);
        vTaskDelay(pdMS_TO_TICKS(backoff_ms));

        if (wifi_retry_count >= WIFI_HARD_RESET_THRESHOLD &&
            wifi_retry_count % WIFI_HARD_RESET_THRESHOLD == 0) {
            ESP_LOGW(TAG, "Hard WiFi reset after %d failures", wifi_retry_count);
            esp_wifi_stop();
            vTaskDelay(pdMS_TO_TICKS(500));
            esp_wifi_start();
        } else {
            esp_wifi_connect();
        }
    }
}

// Boot-only LED status: red slow while searching, red fast when stuck.
// Deletes itself once WIFI_CONNECTED_BIT is set the first time.
static void wifi_status_led_task(void *arg) {
    rgb_color_t red = {.r = 255, .g = 0, .b = 0};
    rgb_color_t off = {.r = 0, .g = 0, .b = 0};
    bool on = false;
    while (1) {
        if (xEventGroupGetBits(wifi_event_group) & WIFI_CONNECTED_BIT) {
            apa102_clear();
            apa102_show();
            vTaskDelete(NULL);
            return;
        }
        on = !on;
        apa102_fill_solid(on ? red : off);
        apa102_show();
        int period_ms = (wifi_led_state == WIFI_LED_STUCK) ? 100 : 500;
        vTaskDelay(pdMS_TO_TICKS(period_ms));
    }
}

static void tx_done_cb(uint8_t ifidx, uint8_t *data, uint16_t *data_len, bool txStatus) {
    send_done = esp_timer_get_time();
    send_time = send_done - send_start;
    send_size = *data_len;
}

// ----- Wi-Fi init -----
static void wifi_init_sta(void) {
    wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_tx_done_cb(tx_done_cb));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
            .sort_method = WIFI_CONNECT_AP_BY_SIGNAL,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    // Launch reconnect-with-backoff task on core 1 (networking core) before
    // esp_wifi_start, so DISCONNECTED events are handled from the very first attempt.
    xTaskCreatePinnedToCore(wifi_reconnect_task, "wifi_reconn", 3072, NULL, 5, NULL, 1);
    // Boot-only LED status so a stuck connect is user-visible instead of a silent hang.
    xTaskCreatePinnedToCore(wifi_status_led_task, "wifi_led", 2048, NULL, 1, NULL, 1);

    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Connecting to WiFi...");
    xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT, false, true, portMAX_DELAY);
    ESP_LOGI(TAG, "WiFi connected");
}

// ----- UDP RX task -----
static void udp_multicast_listen_task(void *arg) {
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Socket creation failed: errno %d", errno);
        vTaskDelete(NULL);
        return;
    }

    struct sockaddr_in local_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(MULTICAST_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(sock, (struct sockaddr *)&local_addr, sizeof(local_addr)) < 0) {
        ESP_LOGE(TAG, "Socket bind failed: errno %d", errno);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    struct ip_mreq mreq = {
        .imr_multiaddr.s_addr = inet_addr(MULTICAST_ADDR),
        .imr_interface.s_addr = htonl(INADDR_ANY)
    };
    if (setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
        ESP_LOGE(TAG, "Failed to join multicast group: errno %d", errno);
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Listening for UDP multicast on port %d", MULTICAST_PORT);

    char rx_buffer[256];
    Send_packet *s_pkt = (Send_packet*)rx_buffer;
    struct sockaddr_in source_addr;
    socklen_t socklen = sizeof(source_addr);
    int level = 0;

    while (1) {
        int len = recvfrom(sock, rx_buffer, sizeof(rx_buffer) - 1, 0, (struct sockaddr *)&source_addr, &socklen);
        if (len < 0) {
            ESP_LOGE(TAG, "recvfrom failed: errno %d", errno);
            continue;
        }

        int64_t local = esp_timer_get_time();
        arrival_time = local;
        int64_t latency = arrival_time - last_arrival_time;
        last_arrival_time = arrival_time;

        // Reset inactivity timers
        last_shake_time = local;

        // Arm one-shot timer for TX alignment
        gptimer_stop(timer);
        gptimer_set_raw_count(timer, 0);
        gptimer_start(timer);

        // We are receiving packets, override the charge indicator and reset watchdog
        override_charge_status_led = true;
        online_watchdog = 0;

        // Parse packet
        int num = s_pkt->packet_num;

        // Slot allocation for us?
        if ((s_pkt->slot_alloc & 0xffffff) == serial) {
            slot_alloc = s_pkt->slot_alloc >> 24;
        }

        // Command handling
        uint8_t cmd = s_pkt->command & 0xff;
        if (cmd == 0x01 && (s_pkt->command >> 8) == serial) {
            ESP_LOGI(TAG, "OTA trigger command received!");
            r_packet.revision |= 0xff000000;
            if (!ota_in_progress) {
                trigger_ota_update();
            }
        } else if (cmd == CMD_PROX_ON) {
            if (!proximity_enabled) {
                proximity_enabled = true;
                ESP_LOGI(ESPNOW_TAG, "Proximity mode enabled");
            }
        } else if (cmd == CMD_PROX_OFF) {
            if (proximity_enabled) {
                proximity_enabled = false;
                ESP_LOGI(ESPNOW_TAG, "Proximity mode disabled");
            }
        } else if (cmd == CMD_SLEEP && (s_pkt->command >> 8) == serial) {
            ESP_LOGI(SLEEP_TAG, "Sleep command received from server");
            apa102_clear();
            apa102_show();
            esp_sleep_enable_ext0_wakeup(LSM_INT_1, 1);
            esp_light_sleep_start();
            last_shake_time = esp_timer_get_time();
            ESP_LOGI(SLEEP_TAG, "Woke from server-triggered sleep");
        } else if (cmd == CMD_MIC_ON && !mic_initialized) {
            ESP_LOGI(MIC_TAG, "Mic enable command received");
            if (setup_mic() == ESP_OK) {
                xTaskCreatePinnedToCore(mic_rms_task, "mic_rms", 4096, NULL, 1, NULL, 0);
            } else {
                ESP_LOGE(MIC_TAG, "Mic init failed — continuing without mic");
            }
        } else if (cmd == CMD_SELF_LED_ON && !self_led_enabled) {
            self_led_enabled = true;
        } else if (cmd == CMD_SELF_LED_OFF && self_led_enabled) {
            self_led_enabled = false;
        }

        // Trigger ESP-NOW broadcast 2ms after multicast RX
        if (proximity_enabled && espnow_timer) {
            esp_timer_stop(espnow_timer);
            esp_timer_start_once(espnow_timer, 2000);
        }

        // Keep sleep logic alive, but skip heavy work while OTA runs
        if (ota_in_progress) {
            online_watchdog = 0;
            continue;
        }

        // Set the LED colours if we have a slot
        if (slot_alloc >= 0 && slot_alloc < 32) {
            uint16_t led_data = s_pkt->led_colour[slot_alloc];
            handle_color_change(led_data);

            rgb_color_t c = {
                .r = (led_data >> 8) & 0xf8,
                .g = (led_data >> 3) & 0xfc,
                .b = (led_data << 3) & 0xf8,
            };

            apa102_fill_solid(c);
            apa102_show();

            // Extract server-controlled bleep parameters from padding
            // Layout: padding bytes 0-31 = note index, bytes 32-63 = bleep period
            uint8_t *audio_data = (uint8_t *)s_pkt->padding;
            uint8_t raw_note   = audio_data[slot_alloc];
            uint8_t raw_period = audio_data[32 + slot_alloc];
            // Bounds check to prevent crashes on mode cycling
            bleep_note   = (raw_note <= 3) ? raw_note : 0;
            bleep_period = (raw_period >= 3 || raw_period == 0) ? raw_period : 0;
        }

        // Build the return packet
        r_packet.revision = ORBVERSION;

        float ax, ay, az;
        if (read_accelerometer_data(&ax, &ay, &az) == ESP_OK) {
            r_packet.accel_x = ax; r_packet.accel_y = ay; r_packet.accel_z = az;
        }

        float gx, gy, gz;
        if (read_gyroscope_data(&gx, &gy, &gz) == ESP_OK) {
            r_packet.gyro_x = gx; r_packet.gyro_y = gy; r_packet.gyro_z = gz;
        }

        r_packet.slot_alloc = serial | ((slot_alloc & 0xff) << 24);

        // Wifi related info
        {
            wifi_ap_record_t ap_info = {0};
            ESP_ERROR_CHECK(esp_wifi_sta_get_ap_info(&ap_info));
            r_packet.rssi = ap_info.rssi;
            r_packet.tx_time = send_time;
            r_packet.pkt_latency = latency;
        }

        // System status
        {
            r_packet.batt_v = last_battery_voltage;
            r_packet.batt_i = last_battery_current;
            // Pack mic level + diagnostics into temperature field
            // Byte layout: [mic_level:8][diag_flags:8][reserved:16]
            uint32_t diag_flags = (espnow_initialized ? 1 : 0) | (proximity_enabled ? 2 : 0) | (mic_initialized ? 4 : 0);
            uint32_t packed_temp = ((uint32_t)mic_level << 24) | (diag_flags << 16);
            memcpy(&r_packet.temperature, &packed_temp, sizeof(packed_temp));
        }

        // Pack ESP-NOW RSSI proximity data
        if (proximity_enabled) {
            pack_rssi_proximity();
        } else {
            memset(r_packet.rssi_proximity, 0, sizeof(r_packet.rssi_proximity));
        }
    }

    close(sock);
    vTaskDelete(NULL);
}

// ----- UDP TX task -----
static void udp_send_task(void *arg) {
    struct sockaddr_in dest_addr = {
        .sin_family = AF_INET,
        .sin_port = htons(MULTICAST_PORT),
        .sin_addr.s_addr = inet_addr(SERVER_IP),
    };
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    assert(sock >= 0);
    int loops = 0;

    for (;;) {
        if (xSemaphoreTake(send_semaphore, portMAX_DELAY) == pdTRUE) {
            if (ota_in_progress) {
                // Drain and skip sends during OTA to reduce traffic/timing jitter
                continue;
            }
            send_start = esp_timer_get_time();
            r_packet.sequence = loops++;
            sendto(sock, &r_packet, rpacket_size, 0, (struct sockaddr*)&dest_addr, sizeof(dest_addr));
        }
    }
}

// ----- Misc init -----
void config_gpio() {
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << GPIO_TIMED),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&io_conf);
    io_conf.pin_bit_mask = 1 << GPIO_BROADCAST;
    gpio_config(&io_conf);
}

void config_timer() {
    send_semaphore = xSemaphoreCreateBinary();
    assert(send_semaphore);

    gptimer_config_t cfg = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1 * 1000 * 1000,
        .intr_priority = 1,
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&cfg, &timer));
    gptimer_event_callbacks_t cbs = { .on_alarm = on_alarm_cb };
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(timer, &cbs, NULL));
    gptimer_alarm_config_t alarm = {
        .alarm_count = DELAY_US,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = false,
    };
    ESP_ERROR_CHECK(gptimer_set_alarm_action(timer, &alarm));
    ESP_ERROR_CHECK(gptimer_enable(timer));
}

static void i2c_master_init() {
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = 9,
        .scl_io_num = 10,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 400000
    };
    i2c_param_config(I2C_NUM_0, &conf);
    i2c_driver_install(I2C_NUM_0, conf.mode, 0, 0, 0);
}

static void check_if_online_task(void *arg) {
    const TickType_t interval = pdMS_TO_TICKS(100);
    TickType_t last_wake_time = xTaskGetTickCount();

    while(true) {
        online_watchdog++;
        if (online_watchdog > ONLINE_WATCHDOG_SILENT_THRESHOLD && override_charge_status_led == true) {
            override_charge_status_led = false;
            slot_alloc = -1;
            apa102_clear();
            apa102_show();
        }
        vTaskDelayUntil(&last_wake_time, interval);
    }
}

// Port of the server's adaptive-gap flood-fill clustering (see
// MODE_AWARENESS block around line 1100 of multicast_sender.cpp). Uses the
// same mutual-strength matrix logic so orbs agree with each other and,
// when the server returns, with the server's view.
//
// On entry `my_slot` is where this orb places itself in the matrix. The
// function writes cluster_id[s] for every known slot (-1 for unknown),
// returning the number of clusters found.
static int compute_cluster_ids(int my_slot, int cluster_id[MAX_ORBS]) {
    // Persistent EWMA-smoothed symmetric matrix. Raw pair strengths are noisy
    // enough that borderline pairs flip across the adaptive-gap threshold
    // frame-to-frame; smoothing at the input makes the gap detection stable.
    // alpha = 0.1 at 50Hz gives half-life ~70ms, full settle ~400ms.
    static uint8_t smoothed_sym[MAX_ORBS][MAX_ORBS] = {{0}};
    uint8_t sym[MAX_ORBS][MAX_ORBS] = {0};
    bool known[MAX_ORBS] = {false};

    int64_t now = esp_timer_get_time();
    if (xSemaphoreTake(rssi_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        // My own row: derive from rssi_matrix on the fly.
        for (int b = 0; b < MAX_ORBS; b++) {
            if (b == my_slot) continue;
            int8_t rv = rssi_matrix[b];
            if (rv <= -127) continue;
            if ((now - rssi_last_seen[b]) > RSSI_STALE_US) continue;
            int val = (((int)rv + 100) * 255) / 100;   // de-saturated (match compute_topN_strengths)
            if (val < 0) val = 0;
            if (val > 255) val = 255;
            peer_matrix[my_slot][b] = (uint8_t)val;
        }
        peer_matrix_last_seen[my_slot] = now;

        // Known slots = anyone whose broadcast we've heard recently.
        for (int a = 0; a < MAX_ORBS; a++) {
            if (peer_matrix_last_seen[a] > 0 && (now - peer_matrix_last_seen[a]) <= RSSI_STALE_US) {
                known[a] = true;
            }
        }

        // Raw sym: avg of both directions when present, max if only one.
        for (int a = 0; a < MAX_ORBS; a++) {
            if (!known[a]) continue;
            for (int b = a + 1; b < MAX_ORBS; b++) {
                if (!known[b]) continue;
                uint8_t ab = peer_matrix[a][b];
                uint8_t ba = peer_matrix[b][a];
                uint8_t v = (ab && ba) ? (uint8_t)((ab + ba) / 2) : (ab > ba ? ab : ba);
                sym[a][b] = sym[b][a] = v;
            }
        }
        xSemaphoreGive(rssi_mutex);
    }

    // EWMA toward raw with symmetric rounding. 5% step at 50Hz -> tau ~400ms,
    // ~1s settling. Pairs that vanish decay toward 0 over roughly 1-2s.
    for (int a = 0; a < MAX_ORBS; a++) {
        for (int b = a + 1; b < MAX_ORBS; b++) {
            int prev = smoothed_sym[a][b];
            int raw  = sym[a][b];
            int blend = (prev * 19 + raw + 10) / 20;  // (19 prev + 1 raw) / 20 ≈ 5% step
            if (blend < 0) blend = 0;
            if (blend > 255) blend = 255;
            smoothed_sym[a][b] = smoothed_sym[b][a] = (uint8_t)blend;
            sym[a][b] = sym[b][a] = (uint8_t)blend;
        }
    }

    // Collect pairwise strengths for gap detection.
    uint8_t strengths[MAX_ORBS * (MAX_ORBS - 1) / 2];
    int ns = 0;
    for (int a = 0; a < MAX_ORBS; a++) {
        if (!known[a]) continue;
        for (int b = a + 1; b < MAX_ORBS; b++) {
            if (!known[b]) continue;
            if (sym[a][b] > 0) strengths[ns++] = sym[a][b];
        }
    }
    // Insertion sort (n is small, typically <30).
    for (int i = 1; i < ns; i++) {
        uint8_t x = strengths[i];
        int j = i;
        while (j > 0 && strengths[j - 1] > x) { strengths[j] = strengths[j - 1]; j--; }
        strengths[j] = x;
    }

    uint8_t cluster_thresh = 230;
    if (ns >= 4) {
        int mid = ns / 2;
        int best_gap = 0;
        for (int i = mid + 1; i < ns; i++) {
            int gap = strengths[i] - strengths[i - 1];
            if (gap > best_gap) {
                best_gap = gap;
                cluster_thresh = (strengths[i - 1] + strengths[i]) / 2;
            }
        }
        if (best_gap <= 5) cluster_thresh = strengths[mid];
    }

    for (int i = 0; i < MAX_ORBS; i++) cluster_id[i] = -1;
    int num_clusters = 0;
    int stack[MAX_ORBS];
    for (int v = 0; v < MAX_ORBS; v++) {
        if (!known[v] || cluster_id[v] >= 0) continue;
        int cid = num_clusters++;
        int top = 0;
        stack[top++] = v;
        while (top > 0) {
            int cur = stack[--top];
            if (cluster_id[cur] >= 0) continue;
            cluster_id[cur] = cid;
            for (int u = 0; u < MAX_ORBS; u++) {
                if (!known[u] || cluster_id[u] >= 0) continue;
                if (sym[cur][u] >= cluster_thresh) {
                    if (top < MAX_ORBS) stack[top++] = u;
                }
            }
        }
    }
    return num_clusters;
}

// Standalone render: when the server is silent, drive LEDs from local accel
// and nearby-peer RSSI so the orb still feels alive with no server running.
// Backs off as soon as multicast resumes or while OTA/charging own the LEDs.
//
// Clustering uses the study-branch server algorithm ported to the orb:
// every orb broadcasts its top-6 RSSI view, everyone assembles the same
// NxN mutual-strength matrix, finds the biggest gap in the upper half of
// pairwise strengths, and flood-fills connected components above that
// threshold. Hue is a golden-ratio hash of cluster id so sibling
// clusters land on well-separated colours.
#define CLUSTER_RSSI_THRESHOLD (-55)  // (legacy, used only by logs below)

static void standalone_render_task(void *arg) {
    const TickType_t interval = pdMS_TO_TICKS(20);  // 50 Hz
    TickType_t last_wake = xTaskGetTickCount();
    int log_counter = 0;

    while (1) {
        vTaskDelayUntil(&last_wake, interval);

        if (online_watchdog <= ONLINE_WATCHDOG_SILENT_THRESHOLD) continue;
        if (ota_in_progress) continue;
        if (battery_charging) continue;

        float ax, ay, az;
        if (read_accelerometer_data(&ax, &ay, &az) != ESP_OK) continue;

        float mag = sqrtf(ax*ax + ay*ay + az*az);
        float shake = fabsf(mag - 1.0f);
        if (shake > 1.5f) shake = 1.5f;

        int my_slot = (slot_alloc >= 0 && slot_alloc < MAX_ORBS)
                      ? slot_alloc : self_slot_effective();

        int cluster_id[MAX_ORBS];
        int num_clusters = compute_cluster_ids(my_slot, cluster_id);
        int my_cid = cluster_id[my_slot];
        if (my_cid < 0) my_cid = 0;

        int raw_size = 0;
        for (int i = 0; i < MAX_ORBS; i++) if (cluster_id[i] == my_cid) raw_size++;

        // Cluster head = lowest slot in my cluster. Stable across DFS reorderings.
        int raw_head = MAX_ORBS;
        for (int i = 0; i < MAX_ORBS; i++) {
            if (cluster_id[i] == my_cid && i < raw_head) raw_head = i;
        }
        if (raw_head >= MAX_ORBS) raw_head = my_slot;

        // --- hysteresis ---
        // Only commit a changed head/size after it's been stable for
        // HYSTERESIS_FRAMES consecutive frames. Kills the per-frame flicker
        // seen when RSSI noise pushes pairs across the adaptive gap threshold.
        // 50Hz * 10 frames = 200ms delay before a real move is honoured.
        static int smoothed_head = -1;
        static int smoothed_size = 0;
        static int pending_head = -1;
        static int pending_size = 0;
        static int pending_streak = 0;
        const int HYSTERESIS_FRAMES = 10;

        if (smoothed_head < 0) {
            smoothed_head = raw_head;
            smoothed_size = raw_size;
            pending_head = raw_head;
            pending_size = raw_size;
            pending_streak = 0;
        } else if (raw_head == smoothed_head && raw_size == smoothed_size) {
            pending_streak = 0;
            pending_head = raw_head;
            pending_size = raw_size;
        } else {
            if (raw_head == pending_head && raw_size == pending_size) {
                pending_streak++;
            } else {
                pending_head = raw_head;
                pending_size = raw_size;
                pending_streak = 1;
            }
            if (pending_streak >= HYSTERESIS_FRAMES) {
                smoothed_head = pending_head;
                smoothed_size = pending_size;
                pending_streak = 0;
            }
        }

        int cluster_head = smoothed_head;
        int my_cluster_size = smoothed_size;
        // Explicit 8-colour palette so sibling cluster heads are always
        // visually distinct. Cluster head -> palette index keeps two orbs
        // in the same cluster on the same colour.
        static const float palette_hue[8] = {
            0.00f,  // red
            0.33f,  // green
            0.66f,  // blue
            0.14f,  // yellow
            0.83f,  // magenta
            0.50f,  // cyan
            0.08f,  // orange
            0.77f,  // violet
        };
        float hue = palette_hue[cluster_head & 7] + shake * 0.03f;
        hue -= floorf(hue);

        bool clustered = (my_cluster_size > 1);
        float v = clustered ? 0.85f : 0.15f;
        v += shake * 0.2f;
        if (v > 1.0f) v = 1.0f;
        float sat = 1.0f;

        if (++log_counter >= 50) {
            log_counter = 0;
            // Also surface my own direct view of the 3 strongest peers so we
            // can tell whether raw-signal noise or threshold-shift is driving
            // any remaining instability.
            int top[3][2] = {{-1,-127}, {-1,-127}, {-1,-127}};  // {slot, rssi}
            int64_t nown = esp_timer_get_time();
            if (xSemaphoreTake(rssi_mutex, pdMS_TO_TICKS(5)) == pdTRUE) {
                for (int p = 0; p < MAX_ORBS; p++) {
                    if (rssi_matrix[p] <= -127) continue;
                    if ((nown - rssi_last_seen[p]) > RSSI_STALE_US) continue;
                    for (int t = 0; t < 3; t++) {
                        if (rssi_matrix[p] > top[t][1]) {
                            for (int k = 2; k > t; k--) { top[k][0]=top[k-1][0]; top[k][1]=top[k-1][1]; }
                            top[t][0] = p; top[t][1] = rssi_matrix[p];
                            break;
                        }
                    }
                }
                xSemaphoreGive(rssi_mutex);
            }
            ESP_LOGI(ESPNOW_TAG,
                "standalone: me=%d raw=(h%d,s%d) sm=(h%d,s%d) clusters=%d | top3: %d@%d %d@%d %d@%d",
                my_slot, raw_head, raw_size, smoothed_head, smoothed_size, num_clusters,
                top[0][0], top[0][1], top[1][0], top[1][1], top[2][0], top[2][1]);
        }

        // HSV -> RGB with full saturation support so `sat` can fade toward white.
        float h6 = hue * 6.0f;
        int sector = (int)h6;
        float f = h6 - sector;
        float p = v * (1.0f - sat);
        float q = v * (1.0f - sat * f);
        float t = v * (1.0f - sat * (1.0f - f));
        float r, g, b;
        switch (sector % 6) {
            case 0: r = v; g = t; b = p; break;
            case 1: r = q; g = v; b = p; break;
            case 2: r = p; g = v; b = t; break;
            case 3: r = p; g = q; b = v; break;
            case 4: r = t; g = p; b = v; break;
            default: r = v; g = p; b = q; break;
        }
        rgb_color_t c = {
            .r = (uint8_t)(r * 255.0f),
            .g = (uint8_t)(g * 255.0f),
            .b = (uint8_t)(b * 255.0f),
        };
        apa102_fill_solid(c);
        apa102_show();
    }
}

void get_unique_id() {
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    serial = (mac[3] << 16) | (mac[4] << 8) | mac[5];
    ESP_LOGI("ID>", "Serial ID %06lx", serial);
}

// ----- app_main -----
void app_main(void) {
    ota_task_handle = NULL; // safety

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW("NVS", "NVS partition corrupted, erasing...");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(OTA_TAG, "Firmware ready for OTA updates");

    // Get unique ID from last three bytes of MAC
    get_unique_id();

    // Check why we woke up
    check_wakeup_reason();

    // Configure timer
    config_timer();

    // Initialize APA102 pixels
    apa102_init();

    // Initialize I2C
    i2c_master_init();

    // Setup accelerometer interrupt
    if (setup_lsm6ds_interrupt() != ESP_OK) {
        ESP_LOGE(ACCEL_TAG, "Failed to setup LSM6DS - continuing without accelerometer features");
    }

    // Initialize INA219
    ina219_init(I2C_NUM_0);

    // Connect to WiFi
    wifi_init_sta();

    // Initialize ESP-NOW for proximity sensing
    if (espnow_init() != ESP_OK) {
        ESP_LOGE(ESPNOW_TAG, "ESP-NOW init failed - proximity disabled");
    } else {
        espnow_send_sem = xSemaphoreCreateBinary();
        const esp_timer_create_args_t timer_args = {
            .callback = espnow_timer_cb,
            .arg = NULL,
            .name = "espnow_sync",
        };
        esp_timer_create(&timer_args, &espnow_timer);

        const esp_timer_create_args_t standalone_timer_args = {
            .callback = standalone_espnow_timer_cb,
            .arg = NULL,
            .name = "standalone_espnow",
        };
        esp_timer_create(&standalone_timer_args, &standalone_espnow_timer);
        esp_timer_start_periodic(standalone_espnow_timer, 20000);  // 50Hz, gated inside the cb
    }

    // Start UDP tasks
    xTaskCreatePinnedToCore(udp_multicast_listen_task, "udp_multicast_listen", 4096, NULL, 5, NULL, 1);
    xTaskCreatePinnedToCore(udp_send_task, "udp_send", 4096, NULL, 6, NULL, 1);

    // Start battery management
    xTaskCreatePinnedToCore(battery_management_task, "battery_mgmt", 4096, NULL, 4, NULL, 1);

    // Start online watchdog
    xTaskCreatePinnedToCore(check_if_online_task, "check_if_online", 4096, NULL, 1, NULL, 1);

    // Standalone render — drives LEDs from local accel while server is silent
    xTaskCreatePinnedToCore(standalone_render_task, "standalone", 3072, NULL, 3, NULL, 1);

    // Audio bleep task — core 0, priority 1 (below WiFi at 23)
    xTaskCreatePinnedToCore(audio_bleep_task, "audio_bleep", 4096, NULL, 1, NULL, 0);

    // ESP-NOW broadcast task — core 0, priority 2 (synced to multicast RX via timer)
    if (espnow_initialized) {
        xTaskCreatePinnedToCore(espnow_broadcast_task, "espnow_bcast", 3072, NULL, 2, NULL, 0);
    }
}
