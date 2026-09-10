// nOdes E6 — passive ESP-NOW sniffer.
//
// Logs every inter-orb proximity broadcast so the fleet's decentralised state
// can be reconstructed offline WITHOUT the server. This exists because the
// obvious instrument does not work: the orb uplink is driven by a one-shot
// gptimer re-armed on each multicast RX (hello_world_main.c:1727), so the
// moment the server stops transmitting the unicast telemetry stream stops too.
// The inter-orb ESP-NOW broadcast keeps running (standalone_espnow_timer_cb),
// and its payload is exactly the input to compute_cluster_ids.
//
// This node NEVER transmits. It cannot appear in any orb's RSSI matrix and so
// cannot perturb what it measures.
//
// Frame (ESPNOW_FRAME_SIZE = 17, see hello_world_main.c:248):
//   [ slot(1) | serial(3) | num_peers(1) | {slot,strength} x 6 (12) ]
//
// Output, one line per received frame, on the console UART:
//   F <us_since_boot> <rssi_dbm> <frame_hex34>
// Anything not starting with "F " is diagnostic and is ignored by e6_capture.py.
//
// FLASH OVER USB TO A TEST BOARD ONLY. Never OTA this to a fleet orb.

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_now.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "soc/soc_caps.h"

#define WIFI_SSID   CONFIG_E6_WIFI_SSID
#define WIFI_PASS   CONFIG_E6_WIFI_PASS
#define FRAME_SIZE  17

static const char *TAG = "e6sniff";
static volatile uint32_t rx_count = 0;

// Live coverage readout. A tethered sniffer hears the whole room in practice,
// but a silent gap -- one orb never heard -- would only surface in analysis,
// after the session. Tracking distinct serials lets the operator confirm the
// count matches the fleet BEFORE committing to a run.
#define MAX_SEEN 64
static uint32_t seen_serial[MAX_SEEN];
static uint32_t seen_count_frames[MAX_SEEN];
static volatile int n_seen = 0;

static void note_serial(uint32_t s) {
    for (int i = 0; i < n_seen; i++) {
        if (seen_serial[i] == s) { seen_count_frames[i]++; return; }
    }
    if (n_seen < MAX_SEEN) {
        seen_serial[n_seen] = s;
        seen_count_frames[n_seen] = 1;
        n_seen++;
    }
}

static const uint8_t bcast[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};

static void hexify(const uint8_t *b, int n, char *out) {
    static const char *H = "0123456789abcdef";
    for (int i = 0; i < n; i++) { out[2*i] = H[b[i] >> 4]; out[2*i+1] = H[b[i] & 0xF]; }
    out[2*n] = 0;
}

static void recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
    if (!info || !info->rx_ctrl || len < FRAME_SIZE) return;
    char frame[2*FRAME_SIZE + 1];
    hexify(data, FRAME_SIZE, frame);
    note_serial(((uint32_t)data[1] << 16) | ((uint32_t)data[2] << 8) | data[3]);
    // printf from the callback is acceptable here: the console is the only
    // consumer and dropping a line under load is preferable to blocking WiFi.
    // No MAC: the 24-bit serial is bytes 1..3 of the payload, and at ~1000
    // frames/s every dropped byte is UART budget we need.
    printf("F %lld %d %s\n", esp_timer_get_time(),
           (int)info->rx_ctrl->rssi, frame);
    rx_count++;
}

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START)        esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "disconnected, retrying");
        esp_wifi_connect();
    }
}

void app_main(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                        &on_wifi, NULL, NULL));
    wifi_config_t wc = {0};
    strncpy((char *)wc.sta.ssid, WIFI_SSID, sizeof(wc.sta.ssid) - 1);
    strncpy((char *)wc.sta.password, WIFI_PASS, sizeof(wc.sta.password) - 1);
    // ESP32-C5 and other dual-band parts can associate on 5 GHz, where they
    // would never hear the fleet: the orbs are ESP32-S3, 2.4 GHz only, and
    // ESP-NOW rides the AP's 2.4 GHz channel. If OrbAP is broadcast on both
    // bands (the Ruckus R500 is dual-band), a C5 sniffer could silently join
    // the 5 GHz BSS and report zero frames, which looks exactly like a
    // coverage failure. Pin the band before associating.
#if SOC_WIFI_SUPPORT_5G
    ESP_ERROR_CHECK(esp_wifi_set_band_mode(WIFI_BAND_MODE_2G_ONLY));
    ESP_LOGI(TAG, "dual-band part: pinned to 2.4 GHz");
#endif
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Associating with OrbAP is what puts us on the fleet's ESP-NOW channel.
    // Do not set the channel by hand; the AP owns it.

    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(recv_cb));
    esp_now_peer_info_t peer = {0};
    memcpy(peer.peer_addr, bcast, 6);
    peer.channel = 0;            // 0 = whatever channel the station is on
    peer.ifidx   = WIFI_IF_STA;
    peer.encrypt = false;
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    ESP_LOGI(TAG, "sniffer up (RX only, never transmits)");

    uint32_t last = 0;
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        uint8_t ch = 0; wifi_second_chan_t sec;
        esp_wifi_get_channel(&ch, &sec);
        // Rate and channel go to the log, not the F-stream, so the host parser
        // can verify we are on the orbs' channel while capturing.
        if (ch > 14) {
            ESP_LOGE(TAG, "channel %u is 5 GHz -- the fleet is 2.4 GHz ONLY. "
                          "No orb frames will ever arrive. Fix the AP/band.", ch);
        }
        ESP_LOGI(TAG, "ch=%u orbs_heard=%d frames=%lu (+%lu in 5s)", ch, n_seen,
                 (unsigned long)rx_count, (unsigned long)(rx_count - last));
        // Per-orb frame counts: a serial with a much lower count than its peers
        // is being heard poorly, which biases its links in the reconstruction.
        for (int i = 0; i < n_seen; i++) {
            printf("H %06lx %lu\n", (unsigned long)seen_serial[i],
                   (unsigned long)seen_count_frames[i]);
        }
        last = rx_count;
    }
}
