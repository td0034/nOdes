#include <iostream>
#include <thread>
#include <atomic>
#include <chrono>
#include <mutex>
#include <ctime>
#include <cstring>
#include <cerrno>
#include <vector>
#include <pthread.h>
#include <sched.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <fmt/core.h>
#include <map>
#include <deque>
#include <fstream>
#include <sstream>
#include <ncurses.h>
#include <signal.h>
#include <Eigen/Dense>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <ifaddrs.h>

#include <set>
#include <map>

#include <chrono>
#include <cmath>
#include <algorithm>

#include <complex>
#include <vector>

#include <nlohmann/json.hpp>
using json = nlohmann::json;

constexpr int spacket_size      = 128;
constexpr int cpacket_size      = 128;
// Max RSSI-proximity neighbours an orb may report. Orbs send top-6 (fleet) up to
// top-PROX_MAX (E1 neighbour-count ablation); the server reads however many the
// packet carries (n_prox = (size-64)/2), so mixed fleets coexist. Packet head is
// 64 bytes, so max report packet = 64 + 2*PROX_MAX bytes.
constexpr int PROX_MAX          = 16;
// @orb-version: 2.6
constexpr char multicast_ip[]   = "239.255.0.1";
constexpr int port              = 5000;
constexpr int bridge_port       = 5002;  // MODE_BRIDGE_AV colour ingress (loopback)
constexpr char local_ip[]       = "0.0.0.0";
constexpr char recv_ip[]        = "0.0.0.0";
// Orbs have this hardcoded in firmware as SERVER_IP. If we don't own it,
// their unicast replies go into the void and the server looks dead.
constexpr char server_ip[]      = "10.0.0.8";
constexpr int64_t silence_alarm_us = 3'000'000;  // 3s

constexpr int max_orbs          = 32;

// --unicast: send the 50Hz fan-out as per-orb unicast instead of relying on
// the AP to convert multicast (the Ruckus does DMS; the Pi 4's brcmfmac AP
// cannot, and raw multicast goes out at the lowest basic rate with no ACK).
// Multicast still goes out every Nth frame so undiscovered orbs can join.
bool unicast_mode = false;
constexpr int discovery_mcast_interval = 10;  // frames between discovery multicasts

// Send-rate control (see ~/network_testing/README.md). --rate sets a fixed loop
// rate; --adaptive slows the loop as orbs join to hold a fixed round-trip budget
// (orbs*Hz) so every orb keeps a consistent update rate instead of the weak ones
// starving. The fleet knee is ~12 orbs @ 50Hz => budget ~600 round-trips/sec.
double g_rate_hz   = 50.0;   // --rate N    fixed loop rate (also the adaptive ceiling)
bool   g_adaptive  = false;  // --adaptive  slow the loop as orbs join
double g_rmin_hz   = 25.0;   // --rmin N    adaptive floor (never slower than this)
double g_budget_rt = 600.0;  // --budget N  round-trips/sec target for adaptive

// --autorate: closed-loop control that holds the miss ratio near a target by
// backing the loop rate off fast when packets are dropping and creeping it back
// up slowly — but only while the fleet is idle (MODE_COMMS), so we never steal
// rate from an active performance. A deadband + minimum dwell give it hysteresis
// so it settles instead of oscillating against the (heavily smoothed) miss
// signal. Ceiling is g_rate_hz; floor is g_armin_hz.
bool   g_autorate     = false; // --autorate  hold miss ratio at the target
double g_autorate_tgt = 0.10;  // --miss N    target miss ratio
double g_armin_hz     = 20.0;  // --armin N   auto-rate floor (lowest acceptable)
double g_act_thr      = 0.05;  // --actthr N  DEPRECATED: no longer gates recovery
                               // (kept so old launch scripts don't break)
// Asymmetric hysteresis around --miss target (default 0.10): ramp up while the
// miss ratio sits below target-UP_BAND (0.09), back off above target+DOWN_BAND
// (0.12). Recovery is NO LONGER gated on fleet-at-rest — Keynsham showed that
// gate pins the rate at the floor for whole sessions (orbs are, by design,
// always in use), leaving 22% of frames <5% miss yet stuck at 10 Hz. The miss
// signal itself is the only arbiter: if 50 Hz is genuinely too fast for the
// current fleet/RF conditions, miss crosses DOWN_BAND and we back off again.
constexpr double AUTORATE_UP_BAND   = 0.01;     // ramp-up margin below target
constexpr double AUTORATE_DOWN_BAND = 0.02;     // back-off margin above target
constexpr double AUTORATE_DOWN_MULT = 0.85;     // multiplicative back-off (fast)
constexpr double AUTORATE_UP_STEP   = 1.0;      // additive recovery, Hz (slow)
constexpr int64_t AUTORATE_DWELL_US = 1500000;  // min interval between adjustments

// Discovery probe sweep (unicast mode): brcmfmac's AP-mode delivery of
// group-addressed frames to power-saving stations is unreliable, so an
// associated-but-undiscovered orb can miss the discovery multicast forever.
// Unicast is ACK'd/retried and delivered via TIM even to sleeping clients,
// so we also walk the DHCP pool one address per frame — full sweep every 2s.
// Range must match the pool in pi_server/30-wlan0.network (PoolOffset/Size).
constexpr int probe_pool_first = 50;   // 10.0.0.50 ..
constexpr int probe_pool_size  = 100;  // .. 10.0.0.149

// TUI constants
constexpr int statstart         = 4;
constexpr int winstart          = statstart + max_orbs;
constexpr int winheight         = 8;
constexpr int cmd_line          = 0;
constexpr int stat_line         = 1;
constexpr int showlat           = 2;
constexpr int headings          = 3;
constexpr int run_ota_limit     = 300;  // Try for 5 seconds
constexpr char commands[]       = "ESC:quit p:program s:show m:mode f:framing z:sleep a:sleep-all q:sleep-still";
constexpr char heading[]        = "   ID         ver       ax     ay     az    gx     gy     gz rssi  batt_v   batt_i   lat  clst colour";


// Sigint handling
volatile sig_atomic_t stop = 0;
void handle_sigint(int sig)
{
    stop = 1;
}

// Returns true if this machine currently owns `want_ip` on some interface.
// Pre-ncurses; prints the list of IPs we do have if the expected one is missing.
bool check_server_ip(const char *want_ip)
{
    struct ifaddrs *list = nullptr;
    if (getifaddrs(&list) != 0) {
        fprintf(stderr, "getifaddrs failed: %s\n", strerror(errno));
        return false;
    }
    bool found = false;
    std::vector<std::string> others;
    for (struct ifaddrs *a = list; a != nullptr; a = a->ifa_next) {
        if (!a->ifa_addr || a->ifa_addr->sa_family != AF_INET) continue;
        char buf[INET_ADDRSTRLEN];
        auto *sin = reinterpret_cast<sockaddr_in *>(a->ifa_addr);
        inet_ntop(AF_INET, &sin->sin_addr, buf, sizeof(buf));
        if (strcmp(buf, want_ip) == 0) found = true;
        else if (strcmp(buf, "127.0.0.1") != 0) {
            others.push_back(std::string(a->ifa_name ? a->ifa_name : "?") + "=" + buf);
        }
    }
    freeifaddrs(list);
    if (!found) {
        fprintf(stderr, "\n*** SERVER IP %s NOT FOUND on any interface ***\n", want_ip);
        fprintf(stderr, "Orbs unicast replies to %s (hardcoded in firmware).\n", want_ip);
        fprintf(stderr, "This machine's IPs: ");
        for (auto &s : others) fprintf(stderr, "%s ", s.c_str());
        fprintf(stderr, "\nFix: sudo ip link set eth0 down && sudo ip link set eth0 up  (or dhclient eth0)\n\n");
        return false;
    }
    return true;
}

// Scrollable region for messages
WINDOW *win;

// convenience function to convert float rgb to 565
uint16_t rgb_to_565(float r, float g, float b)
{
    return (((int)(r * 31)) << 11) | (((int)(g * 63)) << 5) | ((int)(b * 31));
}
uint32_t rgb565_to_888(uint16_t c)
{
    float r = (float)((c >> 11) & 0x1f) / 0x1f;
    float g = (float)((c >> 5) & 0x3f) / 0x3f;
    float b = (float)((c >> 0) & 0x1f) / 0x1f;
    return (((int)(255 * r)) << 16) | ((int)(255 * g) << 8) | ((int)(255 * b));
}

struct SystemState {
    // Phase 1.1 - wait for collective to trigger all notes - 
    bool chaos_achieved = false;
    bool notes_active[12] = {false};
    int active_note_count = 0;

    // Phase 1.2: Root Selection
    bool root_selected = false;
    int note_votes[12] = {0};
    int root_note = -1;
    int fifth_note = -1;
    bool fifth_selected = false;  

    // Phase 1.3 Assign orbs to chord notes based on root (random maj or min)
    int third_note = -1;
    int seventh_note = -1;
    int eleventh_note = -1;
    bool chord_complete = false;
    bool chord_is_major = true;

    std::set<uint32_t> locked_orbs; // Track which orb serial numbers are locked
    std::map<uint32_t, int> orb_locked_notes; // Map orb serial -> locked note

    //Phase 2.0 - orb pulse rates
    // Phase 2: Rhythm detection
    float orb_bpm[32]; // BPM for each orb slot
    float pulse_phase[32]; // Current pulse phase (0.0 to 1.0) for each orb
    float group_average_bpm = 60.0f;
    bool rhythm_sync_achieved = false;
    bool rhythm_phase_active = false;
    
    // Constructor to initialize arrays
    SystemState() {
        // Initialize random BPMs
        for(int i = 0; i < 32; i++) {
            orb_bpm[i] = 20.0f + (rand() % 181); // Random 20-200 BPM
            pulse_phase[i] = (float)(rand() % 100) / 100.0f; // Random starting phase
        }
    }
};

SystemState system_state;

struct Send_packet
{
    uint32_t    packet_num;
    uint32_t    command;
    uint32_t    slot_alloc;
    // Padding to position led_colour array starting at byte 64
    uint32_t    padding[16];
    uint16_t    led_colour[32];
};


// Commands
//  byte 0
//      01  -   OTA on device with serial in upper bytes
//      02  -   Enable proximity mode (ESP-NOW RSSI)
//      03  -   Disable proximity mode
//  byte 3:1    Serial of desired device (OTA only)
constexpr uint8_t CMD_OTA      = 0x01;
constexpr uint8_t CMD_PROX_ON  = 0x02;
constexpr uint8_t CMD_PROX_OFF = 0x03;
constexpr uint8_t CMD_SLEEP    = 0x05;
constexpr uint8_t CMD_MIC_ON       = 0x06;
constexpr uint8_t CMD_SELF_LED_ON  = 0x07;
constexpr uint8_t CMD_SELF_LED_OFF = 0x08;

struct __attribute__((packed)) proximity_entry_t {
    uint8_t slot_id;
    uint8_t strength;
};

struct Recv_packet
{
    uint32_t    slot_alloc; // bits 24-31 are allocated slot, 0xff if no alloc recently
    float       accel_x;    // 4
    float       accel_y;    // 8
    float       accel_z;
    int         rssi;
    int         rx_time;
    int         tx_time;
    float       batt_v;
    float       batt_i;
    uint32_t    pkt_latency;
    float       temperature;       // 40 - 43
    uint32_t    sequence;
    uint32_t    revision;
    float       gyro_x;
    float       gyro_y;
    float       gyro_z;
    // RSSI proximity: top N strongest ESP-NOW peers (bytes 64+). N varies per orb
    // (6 for the fleet, up to PROX_MAX for ablation builds); entries beyond what a
    // given orb reported are zeroed on receipt.
    proximity_entry_t rssi_proximity[PROX_MAX];

    std::string str()
    {
        std::string s = fmt::format("{:08x} {:3d}.{:<3d} {: 6.2f} {: 6.2f} {: 6.2f} {: 6.0f} {: 6.0f} {: 6.0f} {: 4d} {: 8.2f} {: 8.2f} {:5d}",
            slot_alloc,
            (revision>>8)&0xff,
            revision&0xff,
            accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, rssi, batt_v, batt_i, pkt_latency
            );
        return s;
    }

    std::string rssi_prox_str()
    {
        std::string s;
        for (int i = 0; i < PROX_MAX; i++) {
            if (rssi_proximity[i].strength > 0) {
                s += fmt::format("{:2d}:{:3d} ", rssi_proximity[i].slot_id, rssi_proximity[i].strength);
            }
        }
        return s;
    }
};

struct Orb_data
{
    Recv_packet     *rp;
    int64_t         rlatency;
};


pid_t spawn_script(const char *path, char *const argv[])
{
    pid_t pid = fork();
    if (pid < 0)
        return -1;
    if (pid == 0)
    {
        setpgid(0, 0);
        execv(path, argv);
        // exec failed if we get here
        wprintw(win, "Failed to start graph window\n");
        return -1;
    }
    return pid;
}

Eigen::Vector3f blend_colour(Eigen::Vector3f c1, Eigen::Vector3f c2, float r)
{
    return Eigen::Vector3f(
        r * c1[0] + (1 - r) * c2[0], 
        r * c1[1] + (1 - r) * c2[1], 
        r * c1[2] + (1 - r) * c2[2]);
}

using namespace std::chrono;
class RealtimeThreads
{
public:
    RealtimeThreads(
        int                         _priority, 
        int                         _cpu, 
        std::chrono::nanoseconds    _period, 
        std::vector<int>            &_bins
    )
    :   running (false), 
        priority(_priority), 
        cpu     (_cpu), 
        period  (_period), 
        bins    (_bins) {}

    void start()
    {

        // ------------------------------------------------------------------------
        // Create multicast UDP send socket
        sock_send = socket(AF_INET, SOCK_DGRAM, 0);
        if (sock_send < 0)
        {
            perror("Send socket");
            return;
        }

        // Set TTL (time to live) so multicast packets stay on the local subnet
        const int ttl = 1;
        if (setsockopt(sock_send, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl)) < 0)
        {
            perror("setsockopt IP_MULTICAST_TTL");
            close(sock_send);
            return;
        }

        // Setup output interface we are using as multicast. Must be pinned to
        // the orb-facing interface (the one owning server_ip): with 0.0.0.0
        // the kernel routes 239.255.0.1 via the default route, which on a
        // multi-homed host (Pi: wlan0 AP + eth0 uplink) sends discovery
        // multicast out the WRONG interface and orbs never hear the server.
        struct in_addr local_interface;
        inet_pton(AF_INET, server_ip, &local_interface);
        if (setsockopt(sock_send, IPPROTO_IP, IP_MULTICAST_IF, &local_interface, sizeof(local_interface)) < 0)
        {
            perror("setsockopt IP_MULTICAST_IF");
        }

        // Setup destination address for the multicast group
        dest_addr.sin_family    = AF_INET;
        dest_addr.sin_port      = htons(port);
        inet_pton(AF_INET, multicast_ip, &dest_addr.sin_addr);

        // ------------------------------------------------------------------------
        // Create unicast UDP receive socket
        sock_recv   = socket(AF_INET, SOCK_DGRAM, 0);
        if (sock_recv < 0)
        {
            perror("Receive socket");
            return;
        }
        src_addr.sin_family = AF_INET;
        src_addr.sin_port   = htons(port);
        inet_pton(AF_INET, recv_ip, &src_addr.sin_addr);
        if (bind(sock_recv, reinterpret_cast<sockaddr*>(&src_addr), sizeof(src_addr)) < 0) 
        {
            std::perror("Recv bind");
            close(sock_recv);
            return;
        }

        // ------------------------------------------------------------------------
        // Loopback UDP socket for MODE_BRIDGE_AV colour overrides from the
        // python bridge. Local-only; no wireless traffic. Server is
        // authoritative for wireless timing — bridge packets just stage a
        // colour latch the send loop reads.
        sock_bridge = socket(AF_INET, SOCK_DGRAM, 0);
        if (sock_bridge < 0)
        {
            perror("Bridge socket");
        } else {
            sockaddr_in bridge_addr{};
            bridge_addr.sin_family = AF_INET;
            bridge_addr.sin_port   = htons(bridge_port);
            inet_pton(AF_INET, "127.0.0.1", &bridge_addr.sin_addr);
            if (bind(sock_bridge, reinterpret_cast<sockaddr*>(&bridge_addr), sizeof(bridge_addr)) < 0)
            {
                std::perror("Bridge bind");
                close(sock_bridge);
                sock_bridge = -1;
            }
        }

        // ------------------------------------------------------------------------
        // Launch the threads — send/recv real-time, bridge listener plain.
        running     = true;
        worker_send = std::thread(&RealtimeThreads::run_send, this);
        configure_realtime(worker_send.native_handle(), priority, cpu);
        worker_recv = std::thread(&RealtimeThreads::run_recv, this);
        configure_realtime(worker_recv.native_handle(), priority, cpu);
        if (sock_bridge >= 0) {
            worker_bridge = std::thread(&RealtimeThreads::run_bridge_listen, this);
        }
        if (unicast_mode)
            wprintw(win, "Unicast mode: per-orb fan-out at 50Hz, discovery multicast to %s:%d every %d frames\n",
                    multicast_ip, port, discovery_mcast_interval);
        else
            wprintw(win, "Sending multicast packets to %s:%d at 50Hz\n", multicast_ip, port);
    }

    void stop()
    {
        // Signal that threads should stop
        running    = false;
        // Kill any blocking recvfrom calls
        if (sock_recv >= 0)
            shutdown(sock_recv, SHUT_RDWR);
        if (sock_bridge >= 0)
            shutdown(sock_bridge, SHUT_RDWR);

        if (worker_send.joinable())   worker_send.join();
        if (worker_recv.joinable())   worker_recv.join();
        if (worker_bridge.joinable()) worker_bridge.join();
        close(sock_send);
        close(sock_recv);
        if (sock_bridge >= 0) close(sock_bridge);
    }

    ~RealtimeThreads()
    {
        stop();
    }

    std::atomic<bool>   running;
private:
    std::thread         worker_send;
    std::thread         worker_recv;
    std::thread         worker_bridge;
    int                 priority;
    int                 cpu;
    nanoseconds         period;
    double              live_hz = g_rate_hz;   // current loop rate (for TUI + adaptive)
    int64_t             last_autorate_us = 0;  // last --autorate adjustment (dwell gate)
    float               fleet_activity = 0;    // --autorate: fleet motion level (g), all modes
    float               accel_base[max_orbs][3] = {};  // per-slot per-axis |accel| EMA baseline
    bool                accel_seen[max_orbs] = {};     // baseline seeded for this slot?
    float               accel_dev[max_orbs] = {};      // per-orb |accel - baseline| (g), all modes
    float               orb_miss_rate[max_orbs] = {};  // per-orb miss EWMA (0..1)
    int64_t             move_until_us[max_orbs] = {};  // fast-path latch expiry (wall clock)
    bool                fast_active[max_orbs] = {};     // orb currently in motion fast-path window
    // Per-orb cluster-latency measurement (accel onset -> cluster change). Send-thread only.
    int64_t             meas_onset_us[max_orbs] = {};   // wall-clock of motion onset
    int                 meas_onset_cluster[max_orbs] = {}; // cluster at onset
    int                 meas_onset_frame[max_orbs] = {};   // loops at onset
    bool                meas_armed[max_orbs] = {};      // onset latched, awaiting cluster change
    bool                meas_was_quiet[max_orbs] = {};  // refractory: dev fell below threshold since last arm
    FILE*               meas_fp = nullptr;              // /tmp/orb_latency.csv (opened lazily)
    std::vector<int>    &bins;


    sockaddr_in         dest_addr   = {0};
    sockaddr_in         src_addr    = {0};
    // Last-seen IP per slot (network byte order), written by the recv thread,
    // read by the send thread for --unicast fan-out. Orbs always listen on
    // port 5000 regardless of their reply's source port.
    std::atomic<uint32_t> orb_addr[max_orbs] = {};
    int                 sock_send   = -1;
    int                 sock_recv   = -1;
    int                 sock_bridge = -1;

    // MODE_BRIDGE_AV colour latch — the bridge pushes {slot, r, g, b, ttl_ms}
    // packets over loopback UDP (port 5002). Each slot's colour expires after
    // `ttl_ms`; if expired, we fall back to the dim-blue heartbeat so you can
    // always see the server is alive but waiting.
    struct BridgeColour {
        uint8_t r = 0, g = 0, b = 0;
        int64_t expiry_us = 0;
    };
    BridgeColour        bridge_colours[max_orbs];
    std::mutex          bridge_colours_mutex;
    char                message_send[spacket_size];
    char                message_recv[cpacket_size];
    int                 loops       = 0;
    int64_t             next_time_us;
    int64_t             now_us;
    std::atomic<int64_t> last_recv_us{0};  // updated by run_recv on every valid packet

    Send_packet         s_packet;

    Recv_packet         rdata[max_orbs] = {0};
    Orb_data            odata[max_orbs] = {0};

    // Bit map of allocated slots
    uint32_t                slots_allocated;
    // Association of ESP32 serial with slot allocation
    std::map<uint32_t, int> slot_map;
    int                     slot_watchdog[32];
    const int               slot_watchdog_limit = 50;
    std::deque<uint32_t>    slot_queue;

    int                 cursor_line = 0;
    bool                run_ota = false;
    int                 run_ota_loops = 0;
    uint32_t            ota_serial = 0;
    int64_t             elapsed_us = 0;
    int                 show_orb = -1;
    int                 show_orb_timeout;
    uint32_t            sleep_serial = 0;
    int                 sleep_frames = 0;
    // Fleet sleep ('a' all / 'q' quiescent): firmware matches CMD_SLEEP on an
    // exact serial (no broadcast), so multi-orb sleep drains this queue one
    // orb at a time through the existing sleep_serial/sleep_frames machinery.
    std::deque<uint32_t> sleep_queue;

    // Finale light-sync: slots to flash white for N frames, driven by the
    // scale conductor via /tmp/orb_flash_cmd (a chime's orb flashes with it).
    // Separate file from /tmp/orb_prompt_cmd so 30 Hz flash writes can never
    // clobber a pending block/prompt command.
    int     flash_frames[32]        = {0};
    long    flash_cmd_seq           = -1;
    // Finale pad glow: per-slot brightness scaling (0-255, 255 = untouched),
    // streamed by the scale conductor from spin energy via /tmp/orb_glow_cmd.
    // TTL-guarded: if the conductor stops sending, LEDs return to normal.
    int     glow_level[32]          = {0};
    int     glow_ttl                = 0;
    long    glow_cmd_seq            = -1;
    // Finale matched-conditions mode: prompts keep their screen rewards but
    // do NOT touch the orb experience — no compliance-driven LED dimming, no
    // per-prompt viz radius. The glow/flash channels own brightness instead.
    bool    uniform_leds            = false;

    pid_t               graph_pid = -1;
    int                 total_missed_pkts = 0;
    float               missed_packet_ratio = 0;

    // ---- Clustering engine ----
    static constexpr int FEATURE_WINDOW = 50;    // 1 second at 50Hz
    static constexpr int NUM_CLUSTERS = 3;
    static constexpr float MOTION_THRESHOLD = 0.02f;
    static constexpr int HYSTERESIS_FRAMES = 15; // 300ms
    static constexpr float CENTROID_ALPHA = 0.05f;

    struct MotionSample {
        float ax, ay, az, gx, gy, gz;
    };

    struct OrbMotionHistory {
        MotionSample samples[FEATURE_WINDOW] = {};
        int write_idx = 0;
        int count = 0;
        void push(const MotionSample& s) {
            samples[write_idx] = s;
            write_idx = (write_idx + 1) % FEATURE_WINDOW;
            if (count < FEATURE_WINDOW) count++;
        }
    };

    struct MotionFeatures {
        float accel_energy = 0;
        float gyro_energy = 0;
        float jerk_magnitude = 0;
        float axis_ratio = 0;
        bool is_moving = false;
    };

    struct ClusterState {
        Eigen::VectorXf centroids[NUM_CLUSTERS];
        int raw_assignment[max_orbs];       // per-slot raw assignment
        int smoothed_assignment[max_orbs];  // per-slot smoothed (hysteresis)
        int hysteresis_counter[max_orbs];   // per-slot counter
        bool initialized = false;

        ClusterState() {
            for (int i = 0; i < max_orbs; i++) {
                raw_assignment[i] = -1;
                smoothed_assignment[i] = -1;
                hysteresis_counter[i] = 0;
            }
        }
    };

    OrbMotionHistory motion_history[max_orbs];
    MotionFeatures   motion_features[max_orbs];
    ClusterState     cluster_state;

    // Mode: 0 = icosahedron, 1 = cluster, 2 = proximity, 3 = comms, 4 = awareness
    static constexpr int MODE_ICOSAHEDRON = 0;
    static constexpr int MODE_CLUSTER     = 1;
    static constexpr int MODE_PROXIMITY   = 2;
    static constexpr int MODE_COMMS       = 3;
    static constexpr int MODE_AWARENESS   = 4;
    static constexpr int MODE_BRIDGE_AV   = 5;
    static constexpr int NUM_MODES        = 6;
    // Default to PROXIMITY — the prompt/compliance mode the experience runs in
    // (sends CMD_PROX_ON so orbs broadcast RSSI for clustering from boot).
    int              mode = MODE_PROXIMITY;
    static constexpr const char* mode_names[] = {
        "ICOSAHEDRON", "CLUSTER", "PROXIMITY", "COMMS", "AWARENESS", "BRIDGE_AV"
    };

    // Display prompts for the collective-experience loop. Many are SYNONYMS that
    // reuse an existing measurement (see canonical_measure): HOP/LEAP score as
    // JUMP, TWIST/TURN as SPIN, FLOCK as HUDDLE, RAINBOW as SCATTER, TRIANGLE as
    // THREES, STATUE as FREEZE, RATTLE as SHAKE. This expands the pool with zero
    // new detection code. Operator advances with ']' / '[' / 'c' to clear in
    // update_tui(); the session controller can also jump to a prompt by index
    // via /tmp/orb_prompt_cmd (see poll_prompt_cmd).
    static constexpr const char* prompts[] = {
        "JUMP", "HOP", "LEAP",
        "SPIN", "TWIST", "TURN",
        "HUDDLE", "FLOCK",
        "SCATTER", "RAINBOW",
        "PAIRS",
        "THREES", "TRIANGLE",
        "FOURS",
        "FIVES",
        "FREEZE", "STATUE",
        "SHAKE", "RATTLE",
        "WAVE",
    };
    static constexpr int NUM_PROMPTS = 20;
    int current_prompt_idx = -1;   // -1 = no prompt active

    // Map a display label to the canonical MEASURE that scores it. Canonical
    // words (and anything unlisted) map to themselves.
    static std::string canonical_measure(const std::string& label) {
        static const std::map<std::string, std::string> alias = {
            {"HOP", "JUMP"},   {"LEAP", "JUMP"},
            {"TWIST", "SPIN"}, {"TURN", "SPIN"}, {"WHIRL", "SPIN"},
            {"FLOCK", "HUDDLE"}, {"SCRUNCH", "HUDDLE"},
            {"RAINBOW", "SCATTER"}, {"DISPERSE", "SCATTER"},
            {"DUOS", "PAIRS"},
            {"TRIANGLE", "THREES"}, {"TRIOS", "THREES"},
            {"SQUARES", "FOURS"},
            {"STATUE", "FREEZE"}, {"STILL", "FREEZE"},
            {"RATTLE", "SHAKE"}, {"WIGGLE", "SHAKE"},
        };
        auto it = alias.find(label);
        return it == alias.end() ? label : it->second;
    }

    // Target cluster size for the cluster-size measures (0 = not a cluster prompt).
    static int cluster_target_for(const std::string& measure) {
        if (measure == "PAIRS")  return 2;
        if (measure == "THREES") return 3;
        if (measure == "FOURS")  return 4;
        if (measure == "FIVES")  return 5;
        return 0;
    }

    // Whether a measure has a working detector. All current measures score
    // (WAVE got its detector in compute_wave_score); kept for future stubs.
    static bool measure_scoreable(const std::string& measure) {
        (void)measure;
        return true;
    }

    // Poll an external prompt-select command written by the session controller.
    // Format: "<seq> <cmd> [arg]" written atomically (write temp + rename). seq
    // is epoch milliseconds so it is monotonic across controller restarts;
    // commands with seq <= server_start_ms (left over from before this server
    // booted) are ignored. Read every frame — the file is tiny + on tmpfs.
    void poll_prompt_cmd() {
        std::ifstream f("/tmp/orb_prompt_cmd");
        if (!f) return;
        long seq;
        std::string cmd;
        if (!(f >> seq >> cmd)) return;
        if (seq <= server_start_ms) return;    // stale / pre-boot
        if (seq == prompt_cmd_seq) return;      // already applied
        prompt_cmd_seq = seq;
        if (cmd == "set") {
            int idx;
            if ((f >> idx) && idx >= -1 && idx < NUM_PROMPTS) current_prompt_idx = idx;
        } else if (cmd == "clear") {
            current_prompt_idx = -1;
        } else if (cmd == "framing") {
            // `framing collective` / `framing individual` — the study block switch.
            std::string arg;
            if (f >> arg) {
                if      (arg == "collective") framing_collective = true;
                else if (arg == "individual") framing_collective = false;
            }
        } else if (cmd == "block") {
            // `block A1 [collective|individual]` — label, and OPTIONALLY the
            // framing in the same command. Setting both atomically matters:
            // issued as two commands there is a window where the recorded block
            // label and the framing in force disagree, which contaminates every
            // block boundary in the analysis.
            std::string arg;
            if (f >> arg) {
                study_block = arg;
                std::string fr;
                if (f >> fr) {
                    if      (fr == "collective") framing_collective = true;
                    else if (fr == "individual") framing_collective = false;
                }
            }
        } else if (cmd == "proximity") {
            if (mode != MODE_PROXIMITY) {
                mode = MODE_PROXIMITY;
                memset(rssi_matrix, 0, sizeof(rssi_matrix));
            }
        } else if (cmd == "led") {
            // `led uniform` / `led compliance` — finale vs festival/33302
            std::string arg;
            if (f >> arg) {
                if      (arg == "uniform")    uniform_leds = true;
                else if (arg == "compliance") uniform_leds = false;
            }
        }
    }

    // Poll the finale conductor's flash command. Format:
    //   "<seq> flash <slot1,slot2,...> [frames]"
    // Same seq/staleness rules as poll_prompt_cmd, own file + own seq.
    void poll_flash_cmd() {
        std::ifstream f("/tmp/orb_flash_cmd");
        if (!f) return;
        long seq;
        std::string cmd;
        if (!(f >> seq >> cmd)) return;
        if (seq <= server_start_ms) return;
        if (seq == flash_cmd_seq) return;
        flash_cmd_seq = seq;
        if (cmd != "flash") return;
        std::string slots;
        int frames = 8;                      // ~160 ms at 50 Hz
        if (!(f >> slots)) return;
        if (f >> frames) frames = std::max(1, std::min(frames, 250));
        std::stringstream ss(slots);
        std::string tok;
        while (std::getline(ss, tok, ',')) {
            try {
                int s = std::stoi(tok);
                if (s >= 0 && s < 32) flash_frames[s] = frames;
            } catch (...) {}
        }
    }

    // Poll the finale conductor's glow levels. Format:
    //   "<seq> glow <v0,v1,...>"   (0-255 per slot, slot-indexed)
    // Applied as a brightness scale on whatever colour the mode computed;
    // expires after ~2 s without updates so a dead conductor can't dim the rig.
    void poll_glow_cmd() {
        std::ifstream f("/tmp/orb_glow_cmd");
        if (!f) return;
        long seq;
        std::string cmd;
        if (!(f >> seq >> cmd)) return;
        if (seq <= server_start_ms) return;
        if (seq == glow_cmd_seq) return;
        glow_cmd_seq = seq;
        if (cmd != "glow") return;
        std::string vals;
        if (!(f >> vals)) return;
        std::stringstream ss(vals);
        std::string tok;
        int i = 0;
        while (i < 32 && std::getline(ss, tok, ',')) {
            try {
                glow_level[i] = std::max(0, std::min(255, std::stoi(tok)));
            } catch (...) { glow_level[i] = 255; }
            i++;
        }
        for (; i < 32; i++) glow_level[i] = 255;
        glow_ttl = 100;    // 2 s at 50 Hz
    }

    // ===================== Prompt Compliance =====================
    // For every active prompt we compute a per-orb compliance score in
    // [0,1] each frame. The score drives the orb's LED brightness on the
    // device and the orb's radius in the visualiser. Tweakables live in
    // server/prompt_settings.csv (hot-reloaded on mtime change).
    struct PromptSettings {
        // Key -> value, one map per prompt label.
        std::map<std::string, std::map<std::string, float>> by_prompt;
        time_t mtime = 0;

        float get(const std::string& prompt, const std::string& key, float fallback) const {
            auto it = by_prompt.find(prompt);
            if (it == by_prompt.end()) return fallback;
            auto jt = it->second.find(key);
            return jt == it->second.end() ? fallback : jt->second;
        }
    };
    PromptSettings prompt_settings;

    void load_prompt_settings() {
        // The server is launched from server/build (see launch.sh), so also
        // look one level up where prompt_settings.csv actually lives — otherwise
        // every tweakable silently falls back to its code default.
        static const char* candidates[] = {"prompt_settings.csv", "../prompt_settings.csv"};
        const char* path = nullptr;
        struct stat st;
        for (const char* c : candidates) {
            if (stat(c, &st) == 0) { path = c; break; }
        }
        if (!path) return;
        if (st.st_mtime == prompt_settings.mtime) return;
        std::ifstream f(path);
        if (!f) return;
        PromptSettings fresh;
        fresh.mtime = st.st_mtime;
        std::string line;
        bool first = true;
        while (std::getline(f, line)) {
            if (line.empty() || line[0] == '#') continue;
            if (first) { first = false; continue; }  // skip header
            std::stringstream ss(line);
            std::string prompt, key, val;
            if (!std::getline(ss, prompt, ',')) continue;
            if (!std::getline(ss, key, ',')) continue;
            if (!std::getline(ss, val, ',')) continue;
            try { fresh.by_prompt[prompt][key] = std::stof(val); }
            catch (...) {}
        }
        prompt_settings = std::move(fresh);
        wprintw(win, "Prompt settings reloaded\n");
    }

    // ---- Speaker-beacon exclusion ----
    // Beacon orbs parked at the speaker rigs are spatial anchors, not
    // players: they must never count toward the compliance bar, cluster
    // sizes, WAVE, or the player count. Charger-exclusion cannot catch
    // them — a full battery on USB power draws less than the 10 mA
    // detection threshold, so a beacon reads as in-play. Serials come from
    // the same speaker_layout.json the visualiser and orb_replay use
    // (anchor_serial of channels with a left/right/back role), hot-reloaded
    // on mtime change so tools/beacon_id.py assignments land mid-session.
    // The visualiser exempts beacons from its eligibility filter, so they
    // stay on the graph as speaker glyphs.
    std::set<uint32_t> beacon_serials;
    time_t beacon_layout_mtime = 0;

    void load_beacon_serials() {
        const char* env = getenv("ORB_SPEAKER_LAYOUT");
        // Launched from server/build normally; server/ when run by hand.
        const char* candidates[] = {
            env ? env : "",
            "../../sound/71surround/speaker_layout.json",
            "../sound/71surround/speaker_layout.json"};
        const char* path = nullptr;
        struct stat st;
        for (const char* c : candidates) {
            if (c[0] && stat(c, &st) == 0) { path = c; break; }
        }
        if (!path) return;
        if (st.st_mtime == beacon_layout_mtime) return;
        std::ifstream f(path);
        if (!f) return;
        std::set<uint32_t> fresh;
        try {
            json j; f >> j;
            for (const auto& ch : j.value("channels", json::array())) {
                const std::string role = ch.value("role", "");
                if (role != "left" && role != "right" && role != "back") continue;
                if (!ch.contains("anchor_serial") || ch["anchor_serial"].is_null()) continue;
                fresh.insert((uint32_t)std::stoul(
                    ch["anchor_serial"].get<std::string>(), nullptr, 16));
            }
        } catch (...) { return; }   // malformed layout: keep the last good set
        beacon_layout_mtime = st.st_mtime;
        if (fresh != beacon_serials) {
            beacon_serials = std::move(fresh);
            wprintw(win, "Speaker beacons reloaded (%zu excluded from metrics)\n",
                    beacon_serials.size());
        }
    }

    // Per-orb compliance state — persists across frames so we can track
    // stillness/spin sustain timers and decaying jump-apex pulses.
    struct PromptComplianceState {
        int64_t still_since_us = 0;   // FREEZE: monotonic timestamp of when stillness began (0 = not still)
        int64_t spin_since_us  = 0;   // SPIN:   same for sustained spin
        int64_t last_apex_us   = 0;   // JUMP:   last apex detection time (0 = none yet)
        float   shake_smoothed = 0;   // SHAKE:  EWMA of accel peak-to-peak in window
    };
    PromptComplianceState prompt_state[max_orbs];
    float orb_compliance[max_orbs] = {};
    // SHAKE needs a short rolling window of |accel| magnitude per orb;
    // store last 32 samples (~0.64 s at 50 Hz, enough for an 0-600 ms window).
    static constexpr int SHAKE_WIN = 32;
    float shake_buf[max_orbs][SHAKE_WIN] = {};
    int   shake_buf_idx[max_orbs] = {};
    int   shake_buf_count[max_orbs] = {};

    // Per-orb cluster-size map for HUDDLE/PAIRS/THREES/etc. Populated by
    // generate_proximity_colours() before compliance is computed.
    int orb_cluster_size[max_orbs] = {};
    int orb_cluster_id_pc[max_orbs] = {};

    // ---- Group-bar smoothing + topped detection (features 1, 2, 5) ----
    // compliance_mean is published as an asymmetric EMA (fast attack / slow
    // release) so brief clustering glitches don't drop the headline bar.
    float  compliance_mean_smoothed = 0.0f;

    // ---- Study manipulation: feedback framing (ethics ref 33302) ----------
    // The approved adult pilot contrasts COLLECTIVELY-framed blocks (whole-room
    // light feedback, shared goal) against INDIVIDUALLY-framed blocks (own-orb
    // feedback, individual goal), with the physical prompt action matched.
    // INDIVIDUAL is the platform's historical behaviour — each orb's brightness
    // tracks its OWN compliance — so only COLLECTIVE is new: every orb is driven
    // by the group's mean compliance, and the room lights as one body.
    // Switchable at runtime (key 'f' or /tmp/orb_prompt_cmd) so a facilitator
    // never rebuilds between blocks, and published into /tmp/orb_data so the
    // manipulation is VERIFIABLE FROM TELEMETRY rather than merely asserted.
    bool framing_collective = false;
    // Free-text block label from the facilitator (e.g. "A1", "B2", "washout"),
    // echoed into the substrate so telemetry can be cut by block after the fact.
    std::string study_block = "";
    int    compliance_prompt_idx    = -2;     // prompt the smoother is tracking
    bool   prompt_topped_prev       = false;

    // ---- Charger exclusion / eligibility (feature 9) ----
    // A charging orb keeps streaming, so it would otherwise sit in slot_map
    // scoring ~0 and drag the bar down. We exclude it (read-only) from the bar
    // and cluster sizes WITHOUT touching slot_map / the watchdog.
    bool   orb_charging[max_orbs]   = {};     // debounced: orb is on the charger
    int    charge_run[max_orbs]     = {};     // consecutive frames >= charge_ma_on
    int    discharge_run[max_orbs]  = {};     // consecutive frames <= charge_ma_off
    bool   orb_eligible[max_orbs]   = {};     // counts toward bar/clusters this frame
    int    eligible_count           = 0;      // # in-play (not charging)

    // ---- External prompt-select control (/tmp/orb_prompt_cmd) ----
    long    prompt_cmd_seq          = -1;     // last applied command seq (epoch ms)
    int64_t server_start_ms         = 0;      // commands older than this are stale
    json    prompt_catalog_json;              // cached prompt metadata for controller

    // ---- WAVE measure (feature 7) — swarm-level sequential lift ----
    int64_t wave_lift_us[max_orbs]  = {};     // last vigorous-lift time per orb
    float   wave_group_score        = 0.0f;   // shared score (whole-group metric)

    // ===================== Awareness Mode State =====================
    // 5 dimensions from Lee et al. (Living Machines 2025, EMERGE project)
    // Each can be toggled independently via keys 1-5
    static constexpr int AWR_DIM_SELF         = 0;  // orb responds to own motion
    static constexpr int AWR_DIM_SPATIAL      = 1;  // proximity clustering colors
    static constexpr int AWR_DIM_TEMPORAL     = 2;  // motion history / memory
    static constexpr int AWR_DIM_AGENTIVE     = 3;  // active orb influences neighbors
    static constexpr int AWR_DIM_METACOGNITIVE = 4; // cluster stability -> bleep confidence
    static constexpr int AWR_NUM_DIMS         = 5;
    static constexpr const char* awr_dim_names[] = {"SELF", "SPATIAL", "TEMPORAL", "AGENTIVE", "META"};

    bool    awr_dim_active[AWR_NUM_DIMS] = {false, false, false, false, false};
    int     awr_active_count = 0;

    // Per-orb awareness state
    struct AwarenessOrb {
        // Self
        float self_intensity    = 0.0f;  // motion energy (0-1)
        float gravity_brightness= 0.0f;  // icosahedron alignment (0-1)
        int   gravity_vertex    = -1;    // closest icosahedron vertex
        float mic_level         = 0.0f;  // smoothed mic RMS from firmware (0-1)
        float mic_flash         = 0.0f;  // decaying flash multiplier (clap → 1.0 → fade)
        float self_phase        = 0.0f;  // individual breathing phase (0-1)
        float self_bpm          = 40.0f; // individual BPM from own energy
        float gyro_baseline     = -1.0f; // per-orb gyro noise floor (auto-calibrated)
        // Temporal (firefly)
        float firefly_phase     = 0.0f;  // 0-1 phase accumulator
        bool  fired_this_cycle  = false; // phase crossed zero this frame
        // Agentive
        float agentive_received = 0.0f;  // energy received from neighbors
        // Metacognitive
        float meta_stability    = 0.0f;  // how long in same cluster (0-1)
        int   meta_cluster      = -1;    // last known cluster assignment
        int   meta_tenure       = 0;     // frames in current cluster
        // Output
        Eigen::Vector3f color   = {0.0f, 0.0f, 0.0f};
    };
    AwarenessOrb awr_orbs[max_orbs];

    // Firefly state
    float awr_group_bpm    = 40.0f;   // fallback BPM when spatial is off
    float awr_group_energy = 0.0f;    // smoothed mean energy
    float awr_cluster_bpm[max_orbs] = {}; // per-cluster BPM (indexed by cluster_id)

    // NxN RSSI proximity matrix (server-side, built from orb reports)
    uint8_t          rssi_matrix[max_orbs][max_orbs] = {};

    // 3 well-separated cluster hues: red, green, blue (1 LED channel each — battery efficient)
    const Eigen::Vector3f cluster_hues[NUM_CLUSTERS] = {
        {1.0f, 0.0f, 0.0f},  // red   → C6  (1047 Hz)
        {0.0f, 1.0f, 0.0f},  // green → F#6 (1480 Hz)
        {0.0f, 0.0f, 1.0f},  // blue  → D#6 (1245 Hz)
    };
    const Eigen::Vector3f GREY_STATIONARY{0.15f, 0.15f, 0.15f};

    // Audio: cluster tenure tracking for bleep acceleration
    int  cluster_tenure[max_orbs] = {};
    int  last_cluster_id[max_orbs] = {
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,
        -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1
    };

    MotionFeatures extract_features(int slot) {
        MotionFeatures f;
        auto& h = motion_history[slot];
        if (h.count < 10) return f;

        // Compute means
        float mean_amag = 0, mean_gmag = 0;
        float amags[FEATURE_WINDOW], gmags[FEATURE_WINDOW];
        for (int i = 0; i < h.count; i++) {
            auto& s = h.samples[i];
            amags[i] = sqrtf(s.ax*s.ax + s.ay*s.ay + s.az*s.az);
            gmags[i] = sqrtf(s.gx*s.gx + s.gy*s.gy + s.gz*s.gz);
            mean_amag += amags[i];
            mean_gmag += gmags[i];
        }
        mean_amag /= h.count;
        mean_gmag /= h.count;

        // Variance (energy)
        float var_a = 0, var_g = 0;
        for (int i = 0; i < h.count; i++) {
            float da = amags[i] - mean_amag;
            float dg = gmags[i] - mean_gmag;
            var_a += da * da;
            var_g += dg * dg;
        }
        f.accel_energy = var_a / h.count;
        f.gyro_energy = var_g / h.count;

        // Jerk magnitude (mean rate of change of accel)
        float jerk_sum = 0;
        int jerk_n = 0;
        for (int i = 1; i < h.count; i++) {
            int ci = (h.write_idx - h.count + i + FEATURE_WINDOW) % FEATURE_WINDOW;
            int pi = (ci - 1 + FEATURE_WINDOW) % FEATURE_WINDOW;
            float dx = h.samples[ci].ax - h.samples[pi].ax;
            float dy = h.samples[ci].ay - h.samples[pi].ay;
            float dz = h.samples[ci].az - h.samples[pi].az;
            jerk_sum += sqrtf(dx*dx + dy*dy + dz*dz);
            jerk_n++;
        }
        f.jerk_magnitude = jerk_n > 0 ? jerk_sum / jerk_n : 0;

        // Axis ratio: dominant axis variance / total variance
        float var_ax = 0, var_ay = 0, var_az = 0;
        float mean_ax = 0, mean_ay = 0, mean_az = 0;
        for (int i = 0; i < h.count; i++) {
            mean_ax += h.samples[i].ax;
            mean_ay += h.samples[i].ay;
            mean_az += h.samples[i].az;
        }
        mean_ax /= h.count; mean_ay /= h.count; mean_az /= h.count;
        for (int i = 0; i < h.count; i++) {
            float dx = h.samples[i].ax - mean_ax;
            float dy = h.samples[i].ay - mean_ay;
            float dz = h.samples[i].az - mean_az;
            var_ax += dx*dx; var_ay += dy*dy; var_az += dz*dz;
        }
        float total_var = var_ax + var_ay + var_az;
        float max_var = std::max({var_ax, var_ay, var_az});
        f.axis_ratio = total_var > 1e-8f ? max_var / total_var : 0.333f;

        // Moving threshold
        f.is_moving = (f.accel_energy + f.gyro_energy * 0.001f) > MOTION_THRESHOLD;

        return f;
    }

    Eigen::VectorXf features_to_vector(const MotionFeatures& f) {
        Eigen::VectorXf v(4);
        v << logf(f.accel_energy + 1e-6f),
             logf(f.gyro_energy + 1e-6f),
             logf(f.jerk_magnitude + 1e-6f),
             f.axis_ratio;
        return v;
    }

    void run_clustering() {
        // Collect moving orbs
        std::vector<int> moving_slots;
        std::vector<Eigen::VectorXf> fvecs;
        for (auto [k, v] : slot_map) {
            if (motion_features[v].is_moving) {
                moving_slots.push_back(v);
                fvecs.push_back(features_to_vector(motion_features[v]));
            } else {
                cluster_state.raw_assignment[v] = -1;
            }
        }

        if ((int)moving_slots.size() < NUM_CLUSTERS) {
            // Not enough moving orbs for clustering
            for (int s : moving_slots) cluster_state.raw_assignment[s] = 0;
            cluster_state.initialized = false;
            return;
        }

        // K-means++ initialization if needed
        if (!cluster_state.initialized) {
            // Pick first centroid randomly from moving orbs
            int first = rand() % fvecs.size();
            cluster_state.centroids[0] = fvecs[first];

            for (int c = 1; c < NUM_CLUSTERS; c++) {
                // Pick next centroid proportional to squared distance from nearest existing centroid
                std::vector<float> dists(fvecs.size());
                float total_dist = 0;
                for (size_t i = 0; i < fvecs.size(); i++) {
                    float min_d = 1e30f;
                    for (int j = 0; j < c; j++) {
                        float d = (fvecs[i] - cluster_state.centroids[j]).squaredNorm();
                        min_d = std::min(min_d, d);
                    }
                    dists[i] = min_d;
                    total_dist += min_d;
                }
                float r = ((float)rand() / RAND_MAX) * total_dist;
                float cumul = 0;
                int pick = 0;
                for (size_t i = 0; i < fvecs.size(); i++) {
                    cumul += dists[i];
                    if (cumul >= r) { pick = i; break; }
                }
                cluster_state.centroids[c] = fvecs[pick];
            }
            cluster_state.initialized = true;
        }

        // Single-iteration assignment + EMA centroid update
        Eigen::VectorXf centroid_sum[NUM_CLUSTERS];
        int centroid_count[NUM_CLUSTERS] = {};
        for (int c = 0; c < NUM_CLUSTERS; c++)
            centroid_sum[c] = Eigen::VectorXf::Zero(4);

        for (size_t i = 0; i < moving_slots.size(); i++) {
            float min_d = 1e30f;
            int best = 0;
            for (int c = 0; c < NUM_CLUSTERS; c++) {
                float d = (fvecs[i] - cluster_state.centroids[c]).squaredNorm();
                if (d < min_d) { min_d = d; best = c; }
            }
            cluster_state.raw_assignment[moving_slots[i]] = best;
            centroid_sum[best] += fvecs[i];
            centroid_count[best]++;
        }

        // EMA centroid update
        for (int c = 0; c < NUM_CLUSTERS; c++) {
            if (centroid_count[c] > 0) {
                Eigen::VectorXf new_centroid = centroid_sum[c] / centroid_count[c];
                cluster_state.centroids[c] = (1 - CENTROID_ALPHA) * cluster_state.centroids[c] + CENTROID_ALPHA * new_centroid;
            }
        }
    }

    void apply_assignment_hysteresis() {
        for (auto [k, v] : slot_map) {
            int raw = cluster_state.raw_assignment[v];
            int smoothed = cluster_state.smoothed_assignment[v];

            if (raw == smoothed || smoothed == -1) {
                cluster_state.smoothed_assignment[v] = raw;
                cluster_state.hysteresis_counter[v] = 0;
            } else {
                cluster_state.hysteresis_counter[v]++;
                if (cluster_state.hysteresis_counter[v] >= HYSTERESIS_FRAMES) {
                    cluster_state.smoothed_assignment[v] = raw;
                    cluster_state.hysteresis_counter[v] = 0;
                }
            }
        }
    }

    Eigen::Vector3f get_orb_color(int slot) {
        int cluster = cluster_state.smoothed_assignment[slot];
        if (cluster < 0 || !motion_features[slot].is_moving) {
            return GREY_STATIONARY;
        }

        // Brightness proportional to movement intensity (log-scaled)
        float energy = motion_features[slot].accel_energy + motion_features[slot].gyro_energy * 0.001f;
        float log_energy = logf(energy + 1e-6f);
        // Map log_energy from roughly [-12, 0] to [0.15, 1.0]
        float brightness = std::clamp((log_energy + 12.0f) / 12.0f, 0.15f, 1.0f);

        return cluster_hues[cluster % NUM_CLUSTERS] * brightness;
    }

    void generate_cluster_colours() {
        // Push samples into history
        for (auto [k, v] : slot_map) {
            MotionSample ms;
            ms.ax = rdata[v].accel_x;
            ms.ay = rdata[v].accel_y;
            ms.az = rdata[v].accel_z;
            ms.gx = rdata[v].gyro_x;
            ms.gy = rdata[v].gyro_y;
            ms.gz = rdata[v].gyro_z;
            motion_history[v].push(ms);
        }

        // Extract features
        for (auto [k, v] : slot_map) {
            motion_features[v] = extract_features(v);
        }

        // Cluster
        run_clustering();
        apply_assignment_hysteresis();

        // Update cluster tenure + encode audio in packet padding
        // padding layout: bytes 0-31 = note index per slot, bytes 32-63 = bleep period per slot
        uint8_t* audio_notes   = reinterpret_cast<uint8_t*>(&s_packet.padding[0]);
        uint8_t* audio_periods = reinterpret_cast<uint8_t*>(&s_packet.padding[8]);

        for (auto [k, v] : slot_map) {
            int cl = cluster_state.smoothed_assignment[v];
            if (cl >= 0 && motion_features[v].is_moving) {
                if (cl == last_cluster_id[v]) {
                    cluster_tenure[v]++;
                } else {
                    cluster_tenure[v] = 0;
                    last_cluster_id[v] = cl;
                }
                // Bleep accelerates with tenure: 5s → continuous (~33s)
                int period = std::max(1, (int)(250.0f * expf(-(float)cluster_tenure[v] / 300.0f)));
                audio_notes[v]   = (uint8_t)(cl + 1);  // 1=C6, 2=F#6, 3=D#6
                audio_periods[v] = (uint8_t)period;
            } else {
                cluster_tenure[v] = 0;
                last_cluster_id[v] = -1;
                audio_notes[v]   = 0;
                audio_periods[v] = 0;
            }
        }

        // Map colours + encode RGB565
        for (auto [k, v] : slot_map) {
            Eigen::Vector3f c = get_orb_color(v);
            c = c.cwiseMin(1.0f).cwiseMax(0.0f);
            s_packet.led_colour[v] = rgb_to_565(c.x(), c.y(), c.z());
        }

        // JSON export
        json j;
        int cluster_sizes[NUM_CLUSTERS] = {};
        int stationary_count = 0;

        for (auto [k, v] : slot_map) {
            int cl = cluster_state.smoothed_assignment[v];
            if (cl >= 0 && motion_features[v].is_moving)
                cluster_sizes[cl]++;
            if (!motion_features[v].is_moving)
                stationary_count++;

            // Compute centroid distances and membership weights for moving orbs
            std::vector<float> cdist(NUM_CLUSTERS, 0.0f);
            std::vector<float> membership(NUM_CLUSTERS, 0.0f);
            if (motion_features[v].is_moving && cl >= 0 && cluster_state.initialized) {
                Eigen::VectorXf fv = features_to_vector(motion_features[v]);
                float max_neg_dist = -std::numeric_limits<float>::infinity();
                for (int c = 0; c < NUM_CLUSTERS; c++) {
                    float d = (fv - cluster_state.centroids[c]).squaredNorm();
                    cdist[c] = d;
                    if (-d > max_neg_dist) max_neg_dist = -d;
                }
                // Softmax of negative distances
                float sum_exp = 0.0f;
                for (int c = 0; c < NUM_CLUSTERS; c++) {
                    membership[c] = expf(-cdist[c] - max_neg_dist);
                    sum_exp += membership[c];
                }
                if (sum_exp > 0.0f) {
                    for (int c = 0; c < NUM_CLUSTERS; c++)
                        membership[c] /= sum_exp;
                }
            }

            j[fmt::format("{:06x}", k)] = {
                {"accel_x", rdata[v].accel_x},
                {"accel_y", rdata[v].accel_y},
                {"accel_z", rdata[v].accel_z},
                {"gyro_x", rdata[v].gyro_x},
                {"gyro_y", rdata[v].gyro_y},
                {"gyro_z", rdata[v].gyro_z},
                {"features", {
                    {"accel_energy", motion_features[v].accel_energy},
                    {"gyro_energy", motion_features[v].gyro_energy},
                    {"jerk_magnitude", motion_features[v].jerk_magnitude},
                    {"axis_ratio", motion_features[v].axis_ratio},
                    {"is_moving", motion_features[v].is_moving},
                }},
                {"cluster", cl},
                {"cluster_tenure", cluster_tenure[v]},
                {"centroid_dist", cdist},
                {"membership", membership},
                {"batt_v", rdata[v].batt_v},
                {"batt_i", rdata[v].batt_i},
                {"rssi", rdata[v].rssi},
                {"pkt_latency", (int)rdata[v].pkt_latency},
                {"missed", slot_watchdog[v] < slot_watchdog_limit - 1},
                {"miss_rate", orb_miss_rate[v]},
            };
        }

        std::vector<int> csizes(cluster_sizes, cluster_sizes + NUM_CLUSTERS);
        std::vector<std::vector<float>> centroid_vecs;
        for (int c = 0; c < NUM_CLUSTERS; c++) {
            std::vector<float> cv(cluster_state.centroids[c].data(),
                                  cluster_state.centroids[c].data() + cluster_state.centroids[c].size());
            centroid_vecs.push_back(cv);
        }
        j["cluster_status"] = {
            {"num_clusters", NUM_CLUSTERS},
            {"cluster_sizes", csizes},
            {"stationary_count", stationary_count},
            {"centroids", centroid_vecs},
        };

        j["mode"] = mode_names[mode];
        j["current_prompt"] = (current_prompt_idx >= 0 && current_prompt_idx < NUM_PROMPTS)
            ? json(prompts[current_prompt_idx]) : json(nullptr);
        j["network_stats"] = {
            {"miss_ratio", missed_packet_ratio},
            {"rate_hz", live_hz},
            {"activity", fleet_activity},
            {"total_missed_pkts", total_missed_pkts},
            {"num_orbs", (int)slot_map.size()},
            {"frame_num", loops},
            {"jitter_bins", bins},
        };

        // Atomic write
        std::string data = j.dump(4) + "\n";
        int fd = open("/tmp/orb_data_staging", O_CREAT | O_WRONLY | O_TRUNC, 0644);
        write(fd, data.c_str(), data.size());
        fsync(fd);
        close(fd);
        int dirfd = open("/tmp", O_DIRECTORY | O_RDONLY);
        fsync(dirfd);
        close(dirfd);
        rename("/tmp/orb_data_staging", "/tmp/orb_data");
    }

    // ===================== Prompt Compliance Helpers =====================
    // All compliance scoring operates on the most recent rdata sample for
    // accel/gyro and on orb_cluster_size[] (filled before this is called).

    float compute_orb_compliance(int v, const std::string& prompt) {
        float ax = rdata[v].accel_x, ay = rdata[v].accel_y, az = rdata[v].accel_z;
        float gx = rdata[v].gyro_x,  gy = rdata[v].gyro_y,  gz = rdata[v].gyro_z;
        float amag = sqrtf(ax*ax + ay*ay + az*az);
        float gmag = sqrtf(gx*gx + gy*gy + gz*gz);  // deg/s (orb already scaled)
        int64_t now_us = duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count();
        auto& st = prompt_state[v];

        if (prompt == "FREEZE") {
            float a_thr = prompt_settings.get(prompt, "still_accel_g", 0.08f);
            float g_thr = prompt_settings.get(prompt, "still_gyro_dps", 25.0f);
            int   ms    = (int)prompt_settings.get(prompt, "sustain_ms", 1500.0f);
            bool still = (fabsf(amag - 1.0f) < a_thr) && (gmag < g_thr);
            if (!still) { st.still_since_us = 0; return 0.0f; }
            if (st.still_since_us == 0) st.still_since_us = now_us;
            float held_ms = (now_us - st.still_since_us) / 1000.0f;
            return std::clamp(held_ms / std::max(1.0f, (float)ms), 0.0f, 1.0f);
        }
        if (prompt == "SHAKE") {
            int   ms   = (int)prompt_settings.get(prompt, "window_ms", 600.0f);
            float lo   = prompt_settings.get(prompt, "p2p_min_g", 0.4f);
            float hi   = prompt_settings.get(prompt, "p2p_max_g", 2.5f);
            // Push and compute peak-to-peak across the most-recent window samples.
            int n_window = std::min(SHAKE_WIN, std::max(1, ms / 20));  // 20 ms/sample @ 50 Hz
            shake_buf[v][shake_buf_idx[v]] = amag;
            shake_buf_idx[v] = (shake_buf_idx[v] + 1) % SHAKE_WIN;
            if (shake_buf_count[v] < SHAKE_WIN) shake_buf_count[v]++;
            int n = std::min(shake_buf_count[v], n_window);
            float mn = 1e9f, mx = -1e9f;
            for (int i = 0; i < n; i++) {
                int idx = (shake_buf_idx[v] - 1 - i + SHAKE_WIN) % SHAKE_WIN;
                mn = std::min(mn, shake_buf[v][idx]);
                mx = std::max(mx, shake_buf[v][idx]);
            }
            float p2p = mx - mn;
            float inst = std::clamp((p2p - lo) / std::max(1e-3f, hi - lo), 0.0f, 1.0f);
            // Light smoothing so the score doesn't jitter frame-to-frame.
            st.shake_smoothed = 0.7f * st.shake_smoothed + 0.3f * inst;
            return st.shake_smoothed;
        }
        if (prompt == "JUMP") {
            float apex = prompt_settings.get(prompt, "apex_g", 0.55f);
            int   decay = (int)prompt_settings.get(prompt, "decay_ms", 900.0f);
            if (amag < apex) st.last_apex_us = now_us;
            if (st.last_apex_us == 0) return 0.0f;
            float age_ms = (now_us - st.last_apex_us) / 1000.0f;
            return std::clamp(1.0f - age_ms / std::max(1.0f, (float)decay), 0.0f, 1.0f);
        }
        if (prompt == "SPIN") {
            float lo  = prompt_settings.get(prompt, "min_gyro_dps", 150.0f);
            float hi  = prompt_settings.get(prompt, "max_gyro_dps", 500.0f);
            int   ms  = (int)prompt_settings.get(prompt, "sustain_ms", 800.0f);
            if (gmag < lo) { st.spin_since_us = 0; return 0.0f; }
            if (st.spin_since_us == 0) st.spin_since_us = now_us;
            float held_ms = (now_us - st.spin_since_us) / 1000.0f;
            float sustain_score = std::clamp(held_ms / std::max(1.0f, (float)ms), 0.0f, 1.0f);
            float rate_score = std::clamp((gmag - lo) / std::max(1e-3f, hi - lo), 0.0f, 1.0f);
            return sustain_score * rate_score;
        }
        if (prompt == "HUDDLE") {
            int sz = orb_cluster_size[v];
            int total = eligible_count > 0 ? eligible_count : (int)slot_map.size();
            if (total <= 0) return 0.0f;
            return std::clamp(float(sz) / float(total), 0.0f, 1.0f);
        }
        if (prompt == "SCATTER") {
            return orb_cluster_size[v] == 1 ? 1.0f : 0.0f;
        }
        // Cluster-size prompts: PAIRS=2, THREES=3, FOURS=4, FIVES=5.
        if (prompt == "PAIRS" || prompt == "THREES" || prompt == "FOURS" || prompt == "FIVES") {
            int target = (int)prompt_settings.get(prompt, "target_size",
                prompt == "PAIRS" ? 2.0f : prompt == "THREES" ? 3.0f :
                prompt == "FOURS" ? 4.0f : 5.0f);
            int sz = orb_cluster_size[v];
            if (sz == target) return 1.0f;
            float drift = float(std::abs(sz - target)) / std::max(1.0f, (float)target);
            return std::clamp(1.0f - drift, 0.0f, 1.0f);
        }
        // WAVE is a whole-group metric computed once per frame in
        // compute_wave_score(); every orb shares that score.
        if (prompt == "WAVE") return wave_group_score;
        return 0.0f;
    }

    // WAVE: a "wave" is sequential (not simultaneous) lifting across the group.
    // Each eligible orb registers a lift when |accel| spikes past lift_g; the
    // group score rewards BOTH broad participation (most orbs lifted within the
    // window) AND temporal spread (lifts staggered across it — a simultaneous
    // jump has ~zero spread, so it scores ~0, distinguishing WAVE from JUMP).
    void compute_wave_score() {
        int64_t now_us = duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count();
        float lift_g    = prompt_settings.get("WAVE", "lift_g", 1.25f);
        int   window_ms = (int)prompt_settings.get("WAVE", "window_ms", 2500.0f);
        float min_frac  = prompt_settings.get("WAVE", "min_fraction", 0.6f);
        int64_t window_us = (int64_t)window_ms * 1000;

        std::vector<int64_t> lifts;
        int elig = 0;
        for (auto [k, v] : slot_map) {
            if (!orb_eligible[v]) continue;
            elig++;
            float ax = rdata[v].accel_x, ay = rdata[v].accel_y, az = rdata[v].accel_z;
            float amag = sqrtf(ax*ax + ay*ay + az*az);
            if (amag > lift_g) wave_lift_us[v] = now_us;
            if (wave_lift_us[v] != 0 && (now_us - wave_lift_us[v]) <= window_us)
                lifts.push_back(wave_lift_us[v]);
        }
        if (elig <= 1 || lifts.size() < 2) { wave_group_score = 0.0f; return; }

        float frac = (float)lifts.size() / (float)elig;
        int64_t mn = lifts[0], mx = lifts[0];
        for (int64_t t : lifts) { mn = std::min(mn, t); mx = std::max(mx, t); }
        // Spanning half the window earns full spread credit.
        float spread = std::clamp((float)(mx - mn) / (float)(window_us * 0.5f), 0.0f, 1.0f);
        float part   = std::clamp(frac / std::max(0.01f, min_frac), 0.0f, 1.0f);
        wave_group_score = part * spread;
    }

    // ===================== Proximity Mode =====================
    // RSSI proximity matrix decays toward zero every frame so links reflect
    // CURRENT proximity, not the strongest ever seen. Fresh top-6 reports (recv
    // thread) bump entries back to their live strength each report; links that
    // stop being reported — an orb moved away — fade below the cluster
    // threshold within ~1s and the groups separate. Without this the matrix
    // only ever latches the max, so clusters never break apart once formed
    // (the 24-orb "everything is one blob" symptom). Lock-free uint8_t writes:
    // same benign race as the existing send-reads / recv-writes pattern.
    // rssi_decay is live-tunable; 1.0 restores the old no-decay behaviour.
    void decay_rssi_matrix() {
        // Decay factor. With rssi_decay_tau>0 we derive a RATE-INDEPENDENT per-frame
        // factor f = exp(-dt/tau) (dt = 1/live_hz), so f^(t/dt) = e^(-t/tau): a maxed
        // link (255) falls to the cluster floor T in a fixed wall-clock time
        // tau*ln(255/T), the same at 10/20/50Hz. tau<=0 keeps the legacy per-frame
        // rssi_decay (which stretches with rate). Calibration: legacy 0.99@20Hz
        // (~1.2s for 255->200) == tau ~= 5.0s; lower tau to cut cluster latency.
        float tau = prompt_settings.get("GLOBAL", "rssi_decay_tau", -1.0f);
        float d;
        if (tau > 0.0f) {
            d = expf(-(1.0f / (float)live_hz) / tau);
        } else {
            d = prompt_settings.get("GLOBAL", "rssi_decay", 0.97f);
        }
        if (d >= 1.0f) return;
        if (d < 0.0f) d = 0.0f;

        // Optional motion fast-path: links to/from an orb being handled decay faster
        // (fast_decay_tau) so stale links to the group it just left fade in ~0.1s.
        // The recv thread keeps re-bumping still-near peers to live strength, so only
        // genuinely-stale links are suppressed (self-healing).
        float ftau = prompt_settings.get("GLOBAL", "fast_decay_tau", -1.0f);
        float fd = (ftau > 0.0f) ? expf(-(1.0f / (float)live_hz) / ftau) : d;

        for (int a = 0; a < max_orbs; a++)
            for (int b = 0; b < max_orbs; b++) {
                float f = (fast_active[a] || fast_active[b]) ? fd : d;
                rssi_matrix[a][b] = (uint8_t)(rssi_matrix[a][b] * f);
            }
    }

    // Empirical cluster-latency measurement. Per orb: latch a "motion onset" on the
    // rising edge of accel_dev crossing a FIXED threshold (meas_move_g, deliberately
    // independent of the fast-path trigger so the metric isn't circular), then time
    // until the committed cluster_id changes. Emits one CSV record per move to
    // /tmp/orb_latency.csv. Timing uses now_us (steady_clock) so it's rate-agnostic;
    // resolution is ~±2 frames (log hz to bucket). Send-thread only.
    void measure_cluster_latency(const int* cluster_id) {
        if (prompt_settings.get("GLOBAL", "meas_enable", 1.0f) < 0.5f) return;
        float mg   = prompt_settings.get("GLOBAL", "meas_move_g", 0.12f);
        int   tout = (int)prompt_settings.get("GLOBAL", "meas_timeout_ms", 4000.0f);
        // E3 provenance: the decay/debounce config under test is logged on every
        // row, so a sweep that appends multiple (tau, switch_ms) points into one
        // file stays separable and no row can be silently mislabelled.
        float lat_tau  = prompt_settings.get("GLOBAL", "rssi_decay_tau", -1.0f);
        float lat_swms = prompt_settings.get("GLOBAL", "cluster_switch_ms", -1.0f);
        float lat_ftau = prompt_settings.get("GLOBAL", "fast_decay_tau", -1.0f);
        if (!meas_fp) {
            meas_fp = fopen("/tmp/orb_latency.csv", "a");
            if (meas_fp) {
                fseek(meas_fp, 0, SEEK_END);
                if (ftell(meas_fp) == 0) {
                    fprintf(meas_fp, "t_us,serial,dt_ms,from,to,frames,hz,rssi_decay_tau,cluster_switch_ms,fast_decay_tau\n");
                    fflush(meas_fp);
                }
            }
        }
        for (auto [k, v] : slot_map) {
            bool moving = accel_dev[v] > mg;
            if (!meas_armed[v]) {
                // Arm on a rising edge, but only after a quiet period (refractory)
                // so one continuous move yields one measurement.
                if (moving && meas_was_quiet[v]) {
                    meas_armed[v] = true;
                    meas_was_quiet[v] = false;
                    meas_onset_us[v] = now_us;
                    meas_onset_cluster[v] = cluster_id[v];
                    meas_onset_frame[v] = loops;
                }
            } else {
                if (cluster_id[v] != meas_onset_cluster[v] && cluster_id[v] >= 0) {
                    double dt_ms = (now_us - meas_onset_us[v]) / 1000.0;
                    if (meas_fp) {
                        fprintf(meas_fp, "%lld,%06x,%.1f,%d,%d,%d,%.2f,%.2f,%.1f,%.2f\n",
                                (long long)now_us, k, dt_ms,
                                meas_onset_cluster[v], cluster_id[v],
                                loops - meas_onset_frame[v], live_hz,
                                lat_tau, lat_swms, lat_ftau);
                        fflush(meas_fp);
                    }
                    meas_armed[v] = false;
                } else if (now_us - meas_onset_us[v] > (int64_t)tout * 1000) {
                    meas_armed[v] = false;   // abandon: no change within timeout
                }
            }
            if (!moving) meas_was_quiet[v] = true;   // refractory satisfied once still
        }
    }

    void generate_proximity_colours() {
        // Hot-reload CSV tweakables + beacon layout (no-op if mtime unchanged).
        load_prompt_settings();
        load_beacon_serials();

        // Build symmetric NxN distance from rssi_matrix
        // Higher RSSI strength = closer, so treat strength as similarity
        uint8_t sym[max_orbs][max_orbs] = {};
        for (int a = 0; a < max_orbs; a++)
            for (int b = a + 1; b < max_orbs; b++) {
                uint8_t ab = rssi_matrix[a][b], ba = rssi_matrix[b][a];
                // Average if both directions have data, otherwise use whichever exists
                uint8_t val = (ab && ba) ? (uint8_t)((ab + ba) / 2) : std::max(ab, ba);
                sym[a][b] = sym[b][a] = val;
            }

        // For each orb, find nearest neighbor and max RSSI strength
        int nearest[max_orbs];
        uint8_t nearest_str[max_orbs];
        uint8_t max_str[max_orbs];
        memset(nearest, -1, sizeof(nearest));
        memset(nearest_str, 0, sizeof(nearest_str));
        memset(max_str, 0, sizeof(max_str));

        for (auto [k, v] : slot_map) {
            uint8_t best = 0;
            int best_slot = -1;
            for (auto [k2, v2] : slot_map) {
                if (v == v2) continue;
                if (sym[v][v2] > best) {
                    best = sym[v][v2];
                    best_slot = v2;
                }
                if (sym[v][v2] > max_str[v]) max_str[v] = sym[v][v2];
            }
            nearest[v] = best_slot;
            nearest_str[v] = best;
        }

        // Adaptive threshold clustering via connected components.
        // Find the largest gap in the sorted edge strengths to split clusters.
        std::vector<uint8_t> all_strengths;
        for (auto [k1, v1] : slot_map)
            for (auto [k2, v2] : slot_map)
                if (v1 < v2 && sym[v1][v2] > 0)
                    all_strengths.push_back(sym[v1][v2]);
        std::sort(all_strengths.begin(), all_strengths.end());

        // Find threshold: largest gap in the upper half of sorted strengths.
        // The cluster boundary is between "within-cluster" (strong) and
        // "between-cluster" (weaker) links, so we only look above the median.
        uint8_t cluster_thresh = 230; // fallback
        if (all_strengths.size() >= 4) {
            size_t mid = all_strengths.size() / 2;
            int best_gap = 0;
            for (size_t i = mid + 1; i < all_strengths.size(); i++) {
                int gap = all_strengths[i] - all_strengths[i-1];
                if (gap > best_gap) {
                    best_gap = gap;
                    cluster_thresh = (all_strengths[i-1] + all_strengths[i]) / 2;
                }
            }
            // Only split if the gap is meaningful (>5)
            if (best_gap <= 5) cluster_thresh = all_strengths[mid];
        }

        // Smooth the adaptive threshold — frame-to-frame jitter here makes
        // boundary orbs flap between clusters (visible as colour flicker).
        // Threshold smoothing. thresh_ema_tau>0 makes the EWMA rate-independent
        // (a = 1-exp(-dt/tau)); else legacy 0.1 attack. Minor latency term (only
        // shifts cluster boundaries a few RSSI counts) — default stays legacy.
        static float thresh_ema = -1.0f;
        float te_tau = prompt_settings.get("GLOBAL", "thresh_ema_tau", -1.0f);
        float te_a = (te_tau > 0.0f) ? (1.0f - expf(-(1.0f / (float)live_hz) / te_tau)) : 0.10f;
        thresh_ema = (thresh_ema < 0) ? (float)cluster_thresh
                                      : (1.0f - te_a) * thresh_ema + te_a * cluster_thresh;
        cluster_thresh = (uint8_t)(thresh_ema + 0.5f);

        // Absolute floor on the cluster threshold. At 24+ orbs the graph is
        // dense and the adaptive gap can land low enough that weak links bridge
        // distinct groups into one blob. Requiring a minimum within-cluster
        // strength keeps separate groups separate. Live-tunable.
        uint8_t min_thresh = (uint8_t)prompt_settings.get("GLOBAL", "cluster_min_thresh", 200.0f);
        if (cluster_thresh < min_thresh) cluster_thresh = min_thresh;

        // Connected components at the threshold
        int cluster_id[max_orbs];
        memset(cluster_id, -1, sizeof(cluster_id));
        int num_clusters = 0;

        for (auto [k, v] : slot_map) {
            if (cluster_id[v] >= 0) continue;
            // BFS flood fill
            int cid = num_clusters++;
            std::vector<int> stack = {v};
            while (!stack.empty()) {
                int cur = stack.back(); stack.pop_back();
                if (cluster_id[cur] >= 0) continue;
                cluster_id[cur] = cid;
                for (auto [k2, v2] : slot_map) {
                    if (cluster_id[v2] < 0 && sym[cur][v2] >= cluster_thresh)
                        stack.push_back(v2);
                }
            }
        }

        // ---- Stable cluster ids + membership debounce (anti-flicker) ----
        // Raw BFS labels are arbitrary per frame: ids permute even when the
        // grouping barely changes, so hues (and the sound bridge's chords)
        // flicker. Relabel each raw component to the previous-frame id it
        // overlaps most, then debounce per-orb membership so a switch only
        // publishes after persisting cluster_switch_frames consecutive
        // frames. Downstream (hue, JSON, awareness) sees only stable ids.
        {
            static int   stable_id[max_orbs];
            static int   pending_id[max_orbs];
            static float pending_run[max_orbs];   // accumulator: frames OR ms
            static bool init_done = false;
            if (!init_done) {
                memset(stable_id, -1, sizeof(stable_id));
                memset(pending_id, -1, sizeof(pending_id));
                for (int i = 0; i < max_orbs; i++) pending_run[i] = 0.0f;
                init_done = true;
            }
            // Debounce in wall-clock ms (cluster_switch_ms >= 0) so it doesn't stretch
            // when autorate drops Hz; else legacy frame count. A moving orb (fast_active)
            // uses a shorter threshold so it re-clusters quickly once RSSI resettles.
            float dt_ms      = 1000.0f / (float)live_hz;
            float sw_ms      = prompt_settings.get("GLOBAL", "cluster_switch_ms", -1.0f);
            int   sw_frames  = (int)prompt_settings.get("GLOBAL", "cluster_switch_frames", 25.0f);
            float sw_ms_fast = prompt_settings.get("GLOBAL", "cluster_switch_ms_fast", 150.0f);
            bool  use_ms     = sw_ms >= 0.0f;
            float step       = use_ms ? dt_ms : 1.0f;
            float thr_norm   = use_ms ? sw_ms : (float)sw_frames;
            float thr_fast   = use_ms ? sw_ms_fast : std::max(1.0f, sw_ms_fast / dt_ms);

            // overlap[raw][stable]: shared members between this frame's raw
            // components and last frame's stable clusters
            int overlap[max_orbs][max_orbs];
            memset(overlap, 0, sizeof(overlap));
            for (auto [k, v] : slot_map)
                if (cluster_id[v] >= 0 && stable_id[v] >= 0)
                    overlap[cluster_id[v]][stable_id[v]]++;

            int raw_to_stable[max_orbs];
            bool stable_taken[max_orbs] = {};
            for (int r = 0; r < num_clusters; r++) raw_to_stable[r] = -1;
            // Greedy: repeatedly take the largest remaining overlap pair
            for (;;) {
                int best = 0, br = -1, bs = -1;
                for (int r = 0; r < num_clusters; r++) {
                    if (raw_to_stable[r] >= 0) continue;
                    for (int s = 0; s < max_orbs; s++) {
                        if (stable_taken[s]) continue;
                        if (overlap[r][s] > best) { best = overlap[r][s]; br = r; bs = s; }
                    }
                }
                if (br < 0) break;
                raw_to_stable[br] = bs;
                stable_taken[bs] = true;
            }
            // Unmatched raw components get the lowest unused stable id
            for (int r = 0; r < num_clusters; r++) {
                if (raw_to_stable[r] >= 0) continue;
                for (int s = 0; s < max_orbs; s++)
                    if (!stable_taken[s]) { raw_to_stable[r] = s; stable_taken[s] = true; break; }
            }

            // Per-orb debounce, then publish stable ids downstream
            for (auto [k, v] : slot_map) {
                int tgt = (cluster_id[v] >= 0) ? raw_to_stable[cluster_id[v]] : -1;
                float thr = fast_active[v] ? thr_fast : thr_norm;
                if (tgt == stable_id[v]) {
                    pending_run[v] = 0.0f;
                } else if (tgt == pending_id[v]) {
                    if ((pending_run[v] += step) >= thr) {
                        stable_id[v] = tgt;
                        pending_run[v] = 0.0f;
                    }
                } else {
                    pending_id[v] = tgt;
                    pending_run[v] = step;
                }
                cluster_id[v] = stable_id[v];
            }
        }

        // Empirical latency ground truth: time from motion onset to cluster change.
        measure_cluster_latency(cluster_id);

        // Assign well-separated hues per cluster
        float hue[max_orbs] = {};
        // Golden angle spacing for max perceptual separation
        for (auto [k, v] : slot_map) {
            int cid = cluster_id[v];
            if (cid >= 0)
                hue[v] = fmodf(cid * 0.618033988f, 1.0f);
            else
                hue[v] = 0.0f;
        }

        // ---- Charger exclusion / eligibility (debounced) ----
        // An orb on the charger keeps streaming but must not count toward the
        // bar or cluster sizes. Detect via charge current with hysteresis +
        // a sustained-frame gate so a transient spike can't yank an orb out of
        // play mid-prompt. This is a read-only filter — slot_map is untouched.
        float ma_on  = prompt_settings.get("GLOBAL", "charge_ma_on",  10.0f);
        float ma_off = prompt_settings.get("GLOBAL", "charge_ma_off",  5.0f);
        int   on_fr  = (int)prompt_settings.get("GLOBAL", "charge_on_frames",  75.0f);
        int   off_fr = (int)prompt_settings.get("GLOBAL", "charge_off_frames", 50.0f);
        eligible_count = 0;
        for (auto [k, v] : slot_map) {
            float ma = rdata[v].batt_i;
            if (ma >= ma_on)       { if (charge_run[v]    < 1000000) charge_run[v]++;    discharge_run[v] = 0; }
            else if (ma <= ma_off) { if (discharge_run[v] < 1000000) discharge_run[v]++; charge_run[v]    = 0; }
            // else: hysteresis band — hold both counters
            if (!orb_charging[v] && charge_run[v]    >= on_fr)  orb_charging[v] = true;
            if ( orb_charging[v] && discharge_run[v] >= off_fr) orb_charging[v] = false;
            // Speaker beacons are spatial anchors, never players — excluded
            // regardless of charge state (see load_beacon_serials()).
            orb_eligible[v] = !orb_charging[v] && !beacon_serials.count(k);
            if (orb_eligible[v]) eligible_count++;
        }

        // ---- Per-orb compliance (drives LED brightness + viz radius) ----
        // Populate cluster-size lookup so HUDDLE/PAIRS/etc compliance has it.
        // Only ELIGIBLE orbs are counted as cluster members, so a docked orb
        // sitting next to a player doesn't inflate cluster sizes.
        int cluster_size_by_id[max_orbs] = {};
        for (auto [k, v] : slot_map)
            if (cluster_id[v] >= 0 && orb_eligible[v]) cluster_size_by_id[cluster_id[v]]++;
        for (auto [k, v] : slot_map) {
            orb_cluster_id_pc[v] = cluster_id[v];
            orb_cluster_size[v]  = cluster_id[v] >= 0 ? cluster_size_by_id[cluster_id[v]] : 0;
        }
        const bool prompt_active = current_prompt_idx >= 0 && current_prompt_idx < NUM_PROMPTS;
        // display_label is the word shown (may be a synonym); prompt_label is the
        // canonical MEASURE that scores it and keys prompt_settings.csv lookups.
        const std::string display_label = prompt_active ? prompts[current_prompt_idx] : "";
        const std::string prompt_label  = prompt_active ? canonical_measure(display_label) : "";
        // WAVE is a swarm-level metric — compute it once before the per-orb loop.
        if (prompt_active && prompt_label == "WAVE") compute_wave_score();
        for (auto [k, v] : slot_map) {
            orb_compliance[v] = (prompt_active && orb_eligible[v])
                ? compute_orb_compliance(v, prompt_label) : 0.0f;
        }

        // Convert HSV to RGB and set LED colours
        uint8_t* audio_notes   = reinterpret_cast<uint8_t*>(&s_packet.padding[0]);
        uint8_t* audio_periods = reinterpret_cast<uint8_t*>(&s_packet.padding[8]);

        // Per-prompt brightness range from CSV (constant across all orbs for
        // a given frame). When no prompt is active, stay at full brightness.
        float bmin = prompt_active ? prompt_settings.get(prompt_label, "led_brightness_min", 0.15f) : 1.0f;
        float bmax = prompt_active ? prompt_settings.get(prompt_label, "led_brightness_max", 1.0f)  : 1.0f;

        for (auto [k, v] : slot_map) {
            float h = hue[v] * 6.0f;
            // Brightness = lerp(min, max, compliance) when a prompt is active.
            // Study manipulation. COLLECTIVE: every orb reflects the GROUP's
            // compliance, so the whole room brightens and dims together and an
            // individual cannot tell their own contribution apart from the
            // group's. INDIVIDUAL: each orb reflects only its own effort.
            // compliance_mean_smoothed is last frame's value (20 ms at 50 Hz —
            // imperceptible), which keeps the existing bar logic untouched and
            // gives the collective arm free temporal smoothing.
            const float drive = framing_collective ? compliance_mean_smoothed
                                                   : orb_compliance[v];
            float brightness = (prompt_active && !uniform_leds)
                ? (bmin + (bmax - bmin) * drive)
                : 1.0f;

            // HSV to RGB (saturation = 1)
            int hi = (int)h % 6;
            float f = h - floorf(h);
            float q = brightness * (1.0f - f);
            float t = brightness * f;
            float r, g, b;
            switch (hi) {
                case 0: r = brightness; g = t;          b = 0;          break;
                case 1: r = q;          g = brightness; b = 0;          break;
                case 2: r = 0;          g = brightness; b = t;          break;
                case 3: r = 0;          g = q;          b = brightness; break;
                case 4: r = t;          g = 0;          b = brightness; break;
                default:r = brightness; g = 0;          b = q;          break;
            }
            s_packet.led_colour[v] = rgb_to_565(r, g, b);

            // Audio disabled in proximity mode — bleeps cause power sag + WiFi disconnect
            audio_notes[v] = 0;
            audio_periods[v] = 0;
        }

        // JSON export
        json j;
        for (auto [k, v] : slot_map) {
            // Build per-orb rssi_proximity array (all reported neighbours; a
            // top-N ablation orb emits N>6, the fleet emits 6 — zero entries skipped).
            json prox_arr = json::array();
            for (int i = 0; i < PROX_MAX; i++) {
                if (rdata[v].rssi_proximity[i].strength == 0) continue;
                prox_arr.push_back({
                    {"slot", rdata[v].rssi_proximity[i].slot_id},
                    {"str", rdata[v].rssi_proximity[i].strength}
                });
            }

            j[fmt::format("{:06x}", k)] = {
                {"accel_x", rdata[v].accel_x},
                {"accel_y", rdata[v].accel_y},
                {"accel_z", rdata[v].accel_z},
                {"gyro_x", rdata[v].gyro_x},
                {"gyro_y", rdata[v].gyro_y},
                {"gyro_z", rdata[v].gyro_z},
                {"batt_v", rdata[v].batt_v},
                {"batt_i", rdata[v].batt_i},
                {"rssi", rdata[v].rssi},
                {"pkt_latency", (int)rdata[v].pkt_latency},
                {"missed", slot_watchdog[v] < slot_watchdog_limit - 1},
                {"miss_rate", orb_miss_rate[v]},
                // Per-orb firmware revision (the IV for E1's top-N ablation; the
                // orb already sends it, we just surface it so a capture is
                // self-identifying and a partial OTA is detectable post-hoc).
                {"firmware", fmt::format("{}.{}", (rdata[v].revision >> 8) & 0xff, rdata[v].revision & 0xff)},
                {"rssi_proximity", prox_arr},
                {"nearest_neighbor", nearest[v]},
                {"nearest_strength", nearest_str[v]},
                {"hue", hue[v]},
                {"cluster", cluster_id[v]},
                {"compliance", orb_compliance[v]},
                {"charging", orb_charging[v]},
                {"eligible", orb_eligible[v]},
            };
        }

        // Group-level compliance for the headline bar — mean over ELIGIBLE
        // (in-play) orbs only, so docked/charging orbs can't cap it below 1.0.
        float compliance_sum = 0.0f;
        int compliance_n = 0;
        for (auto [k, v] : slot_map) {
            if (!orb_eligible[v]) continue;
            compliance_sum += orb_compliance[v];
            compliance_n++;
        }
        float compliance_raw = compliance_n > 0 ? compliance_sum / compliance_n : 0.0f;

        // Asymmetric EMA: fast attack, slow release. Reset on prompt change /
        // clear so the new prompt starts from an empty bar.
        bool topped = false, just_topped = false;
        float topped_thresh_eff = prompt_settings.get("GLOBAL", "topped_thresh", 0.90f);
        if (!prompt_active) {
            compliance_mean_smoothed = 0.0f;
            prompt_topped_prev = false;
            compliance_prompt_idx = -2;
        } else {
            if (current_prompt_idx != compliance_prompt_idx) {
                compliance_mean_smoothed = 0.0f;
                prompt_topped_prev = false;
                compliance_prompt_idx = current_prompt_idx;
            }
            float a_up = prompt_settings.get("GLOBAL", "smooth_alpha_up",   0.60f);
            float a_dn = prompt_settings.get("GLOBAL", "smooth_alpha_down", 0.05f);
            float a = (compliance_raw > compliance_mean_smoothed) ? a_up : a_dn;
            compliance_mean_smoothed += a * (compliance_raw - compliance_mean_smoothed);
            // Per-prompt, scale-aware compliance bar (Keynsham field report §5):
            // a flat 0.90 makes sustained-physical prompts (JUMP/LEAP/HOP)
            // effectively unwinnable and drops completion as the crowd grows
            // (72%->40%), with most failures near-misses at 0.83-0.89. The base
            // bar is per-measure (fallback GLOBAL.topped_thresh), then relaxed
            // linearly once eligible_orbs exceeds topped_relax_from, down to a
            // per-measure/GLOBAL floor. All knobs hot-reload from the CSV.
            float g_thr = prompt_settings.get("GLOBAL", "topped_thresh", 0.90f);
            float base  = prompt_settings.get(prompt_label, "topped_thresh", g_thr);
            float relax_from = prompt_settings.get("GLOBAL", "topped_relax_from", 6.0f);
            float relax_per  = prompt_settings.get(prompt_label, "topped_relax_per_orb",
                                 prompt_settings.get("GLOBAL", "topped_relax_per_orb", 0.0f));
            float thr_floor  = prompt_settings.get(prompt_label, "topped_thresh_floor",
                                 prompt_settings.get("GLOBAL", "topped_thresh_floor", 0.60f));
            // The floor never rises above the prompt's own base bar, so a
            // deliberately low base (e.g. JUMP 0.60) is not clamped back up by
            // the GLOBAL floor; it only bounds how far relaxation can lower it.
            if (thr_floor > base) thr_floor = base;
            float thr  = base;
            float over = (float)eligible_count - relax_from;
            if (over > 0.0f) thr -= relax_per * over;
            if (thr < thr_floor) thr = thr_floor;
            topped_thresh_eff = thr;
            topped = compliance_mean_smoothed >= thr;
            just_topped = topped && !prompt_topped_prev;
            prompt_topped_prev = topped;
        }
        float compliance_mean = compliance_mean_smoothed;  // published value

        // Full NxN proximity matrix (only active slots)
        json pm;
        for (auto [k1, v1] : slot_map) {
            json row;
            for (auto [k2, v2] : slot_map) {
                row[fmt::format("{}", v2)] = sym[v1][v2];
            }
            pm[fmt::format("{}", v1)] = row;
        }
        j["proximity_matrix"] = pm;

        // Slot-to-serial mapping
        json sm;
        for (auto [k, v] : slot_map)
            sm[fmt::format("{}", v)] = fmt::format("{:06x}", k);
        j["slot_to_serial"] = sm;

        j["mode"] = mode_names[mode];
        // Study manipulation state — recorded every frame so a block's framing
        // can be confirmed from the data, not taken on trust.
        j["framing"] = framing_collective ? "COLLECTIVE" : "INDIVIDUAL";
        j["study_block"] = study_block.empty() ? json(nullptr) : json(study_block);
        j["current_prompt"] = prompt_active ? json(display_label) : json(nullptr);
        j["current_prompt_idx"] = current_prompt_idx;
        j["num_prompts"] = NUM_PROMPTS;
        j["compliance_mean"] = compliance_mean;       // smoothed (headline bar)
        j["compliance_raw"] = compliance_raw;         // unsmoothed, for debugging
        j["prompt_topped"] = topped;                  // bar held at/above threshold
        j["prompt_just_topped"] = just_topped;        // rising edge this frame
        j["eligible_orbs"] = eligible_count;          // in-play count (excludes charging)
        j["topped_thresh_eff"] = topped_thresh_eff;   // per-prompt, scale-aware bar (Keynsham)

        // Cached prompt catalog so the session controller knows each prompt's
        // canonical measure, cluster target, and whether it is scoreable.
        if (prompt_catalog_json.is_null()) {
            json cat = json::array();
            for (int i = 0; i < NUM_PROMPTS; i++) {
                std::string lbl = prompts[i];
                std::string m   = canonical_measure(lbl);
                cat.push_back({
                    {"label", lbl},
                    {"measure", m},
                    {"target", cluster_target_for(m)},
                    {"scoreable", measure_scoreable(m)},
                });
            }
            prompt_catalog_json = cat;
        }
        j["prompts"] = prompt_catalog_json;
        // Per-prompt viz radius range so the visualiser can scale orbs
        // without owning its own copy of the settings file.
        if (prompt_active && !uniform_leds) {
            j["viz_radius_min"] = prompt_settings.get(prompt_label, "viz_radius_min", 0.10f);
            j["viz_radius_max"] = prompt_settings.get(prompt_label, "viz_radius_max", 0.30f);
        }
        j["uniform_leds"] = uniform_leds;
        j["network_stats"] = {
            {"miss_ratio", missed_packet_ratio},
            {"rate_hz", live_hz},
            {"activity", fleet_activity},
            {"total_missed_pkts", total_missed_pkts},
            {"num_orbs", (int)slot_map.size()},
            {"frame_num", loops},
            {"jitter_bins", bins},
        };

        // (Stale "mode" override removed — was clobbering the proper
        // mode_names[mode] above with a lowercase string. Viz now sees
        // "PROXIMITY" consistently.)
        j["cluster_threshold"] = cluster_thresh;
        j["num_clusters"] = num_clusters;

        // Atomic write
        std::string data = j.dump(4) + "\n";
        int fd = open("/tmp/orb_data_staging", O_CREAT | O_WRONLY | O_TRUNC, 0644);
        write(fd, data.c_str(), data.size());
        fsync(fd);
        close(fd);
        int dirfd = open("/tmp", O_DIRECTORY | O_RDONLY);
        fsync(dirfd);
        close(dirfd);
        rename("/tmp/orb_data_staging", "/tmp/orb_data");
    }
    // ===================== End Proximity Mode =====================

    // ===================== Awareness Mode =====================
    // Implements Lee et al. "Framework for Examination of Awareness in Artificial
    // Systems" (Living Machines 2025, EMERGE project). Five awareness dimensions
    // can be toggled independently; participants feel how each capacity changes
    // the collective's behavior.
    //
    // Audio uses existing padding protocol (note 1-3, period 0-255).
    // All audio parameters are written to volatile globals by the firmware RX
    // task — no blocking I2S calls touch the send loop.

    void generate_awareness_colours() {
        // --- Motion feature extraction (reuse from cluster mode) ---
        for (auto [k, v] : slot_map) {
            MotionSample ms;
            ms.ax = rdata[v].accel_x; ms.ay = rdata[v].accel_y; ms.az = rdata[v].accel_z;
            ms.gx = rdata[v].gyro_x;  ms.gy = rdata[v].gyro_y;  ms.gz = rdata[v].gyro_z;
            motion_history[v].push(ms);
            motion_features[v] = extract_features(v);
        }

        // --- Build symmetric RSSI matrix ---
        uint8_t sym[max_orbs][max_orbs] = {};
        for (int a = 0; a < max_orbs; a++)
            for (int b = a + 1; b < max_orbs; b++) {
                uint8_t ab = rssi_matrix[a][b], ba = rssi_matrix[b][a];
                uint8_t val = (ab && ba) ? (uint8_t)((ab + ba) / 2) : std::max(ab, ba);
                sym[a][b] = sym[b][a] = val;
            }

        // --- SPATIAL: adaptive threshold clustering ---
        // MODE_BRIDGE_AV forces clustering even when the SPATIAL awareness
        // dim is off, because the python AV bridge routes orbs to voices by
        // cluster id; without this, every orb reports cluster=-1 and they
        // all collapse onto the same voice.
        int cluster_id[max_orbs];
        memset(cluster_id, -1, sizeof(cluster_id));
        int num_clusters = 0;

        if (awr_dim_active[AWR_DIM_SPATIAL] || mode == MODE_BRIDGE_AV) {
            std::vector<uint8_t> all_strengths;
            for (auto [k1, v1] : slot_map)
                for (auto [k2, v2] : slot_map)
                    if (v1 < v2 && sym[v1][v2] > 0)
                        all_strengths.push_back(sym[v1][v2]);
            std::sort(all_strengths.begin(), all_strengths.end());

            uint8_t cluster_thresh = 230;
            if (all_strengths.size() >= 4) {
                size_t mid = all_strengths.size() / 2;
                int best_gap = 0;
                for (size_t i = mid + 1; i < all_strengths.size(); i++) {
                    int gap = all_strengths[i] - all_strengths[i-1];
                    if (gap > best_gap) {
                        best_gap = gap;
                        cluster_thresh = (all_strengths[i-1] + all_strengths[i]) / 2;
                    }
                }
                if (best_gap <= 5) cluster_thresh = all_strengths[mid];
            }

            for (auto [k, v] : slot_map) {
                if (cluster_id[v] >= 0) continue;
                int cid = num_clusters++;
                std::vector<int> stack = {v};
                while (!stack.empty()) {
                    int cur = stack.back(); stack.pop_back();
                    if (cluster_id[cur] >= 0) continue;
                    cluster_id[cur] = cid;
                    for (auto [k2, v2] : slot_map) {
                        if (cluster_id[v2] < 0 && sym[cur][v2] >= cluster_thresh)
                            stack.push_back(v2);
                    }
                }
            }
        }

        // --- Count active dimensions ---
        awr_active_count = 0;
        for (int d = 0; d < AWR_NUM_DIMS; d++)
            if (awr_dim_active[d]) awr_active_count++;

        // --- Audio command pointers ---
        uint8_t* audio_notes   = reinterpret_cast<uint8_t*>(&s_packet.padding[0]);
        uint8_t* audio_periods = reinterpret_cast<uint8_t*>(&s_packet.padding[8]);

        // =====================================================================
        // First pass: SELF, TEMPORAL, METACOGNITIVE per orb
        // =====================================================================
        float energy_sum = 0.0f;
        int   energy_count = 0;

        for (auto [k, v] : slot_map) {
            auto& ao = awr_orbs[v];
            auto& mf = motion_features[v];

            // --- Extract mic level from firmware (packed in temperature field) ---
            uint32_t temp_raw;
            memcpy(&temp_raw, &rdata[v].temperature, 4);
            float raw_mic = (float)((temp_raw >> 24) & 0xFF) / 255.0f;
            ao.mic_level = 0.85f * ao.mic_level + 0.15f * raw_mic; // smooth on server side too

            // --- SELF: gravity → colour, motion energy → individual breathing ---
            // Track gyro baseline for all orbs (slow adaptation to noise floor)
            if (ao.gyro_baseline < 0.0f) ao.gyro_baseline = mf.gyro_energy;
            else ao.gyro_baseline = 0.999f * ao.gyro_baseline
                                  + 0.001f * std::min(mf.gyro_energy, ao.gyro_baseline * 2.0f);

            if (awr_dim_active[AWR_DIM_SELF]) {
                // Motion energy: accel + gyro above baseline
                float gyro_above = std::max(0.0f, mf.gyro_energy - ao.gyro_baseline) * 0.001f;
                float energy = mf.accel_energy + gyro_above;
                ao.self_intensity = std::clamp((logf(energy + 1e-6f) + 7.0f) / 7.0f, 0.0f, 1.0f);

                // Gravity vector → closest icosahedron vertex (for colour)
                Eigen::Vector3f grav(rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z);
                float grav_norm = grav.norm();
                if (grav_norm > 0.1f) {
                    grav /= grav_norm;
                    ZoneInfo zi = calculate_zone_info(grav);
                    ao.gravity_vertex = zi.best_vertex;
                }

                // Individual breathing: BPM from own energy, phase advances independently
                float self_target = 5.0f + 175.0f * std::clamp(ao.self_intensity * 2.0f, 0.0f, 1.0f);
                ao.self_bpm = 0.95f * ao.self_bpm + 0.05f * self_target;
                ao.self_phase += ao.self_bpm / (60.0f * 50.0f);
                if (ao.self_phase >= 1.0f) ao.self_phase -= 1.0f;
            } else {
                ao.self_intensity = 0.0f;
                ao.self_phase = 0.0f;
            }

            // Motion energy for temporal BPM (accel + gyro above baseline)
            {
                float gyro_above = std::max(0.0f, mf.gyro_energy - ao.gyro_baseline) * 0.001f;
                float e = mf.accel_energy + gyro_above;
                float mapped = std::clamp((logf(e + 1e-6f) + 7.0f) / 7.0f, 0.0f, 1.0f);
                energy_sum += mapped;
            }
            energy_count++;

            // --- TEMPORAL: firefly phase advance (coupling done in second pass) ---
            // BRIDGE_AV forces TEMPORAL on too, so the bridge's firefly-breathe
            // knob has a live phase to modulate. Otherwise firefly_phase would
            // stay at 0 and breathe would be a static offset.
            if (awr_dim_active[AWR_DIM_TEMPORAL] || mode == MODE_BRIDGE_AV) {
                // Advance phase by cluster BPM (or global if spatial off)
                int cl = cluster_id[v];
                float bpm = (awr_dim_active[AWR_DIM_SPATIAL] && cl >= 0)
                          ? awr_cluster_bpm[cl] : awr_group_bpm;
                float phase_inc = bpm / (60.0f * 50.0f);
                ao.firefly_phase += phase_inc;

                // Mic phase reset: loud sound pulls phase toward zero
                if (ao.mic_level > 0.03f) {
                    ao.firefly_phase *= 0.3f; // pull 70% toward zero
                }

                // Detect zero-crossing (phase wraps past 1.0)
                ao.fired_this_cycle = false;
                if (ao.firefly_phase >= 1.0f) {
                    ao.firefly_phase -= 1.0f;
                    ao.fired_this_cycle = true;
                }
            } else {
                ao.firefly_phase = 0.0f;
                ao.fired_this_cycle = false;
            }

            // --- Track cluster changes + METACOGNITIVE stability ---
            if (awr_dim_active[AWR_DIM_SPATIAL]) {
                int cl = cluster_id[v];
                if (cl >= 0 && cl != ao.meta_cluster) {
                    // Cluster changed — randomize phase so clusters desync
                    // Use slot + loop counter for unique seed per orb
                    if (awr_dim_active[AWR_DIM_TEMPORAL])
                        ao.firefly_phase = fmodf((float)((v * 7919 + loops * 13) % 10000) / 10000.0f, 1.0f);
                    ao.meta_tenure = 0;
                    ao.meta_cluster = cl;
                } else if (cl >= 0) {
                    ao.meta_tenure++;
                }
                ao.meta_stability = std::clamp((float)ao.meta_tenure / 250.0f, 0.0f, 1.0f);
            } else {
                ao.meta_stability *= 0.95f; // gentle fade when disabled
                ao.meta_tenure = 0;
            }
        }

        // --- Update BPM: per-cluster when spatial is on, global otherwise ---
        if (awr_dim_active[AWR_DIM_TEMPORAL]) {
            if (awr_dim_active[AWR_DIM_SPATIAL] && num_clusters > 0) {
                // Per-cluster BPM from each cluster's motion energy
                float cl_energy_sum[max_orbs] = {};
                int   cl_energy_count[max_orbs] = {};
                for (auto [k, v] : slot_map) {
                    int cl = cluster_id[v];
                    if (cl >= 0) {
                        float gyro_above = std::max(0.0f, motion_features[v].gyro_energy - awr_orbs[v].gyro_baseline) * 0.001f;
                        float e = motion_features[v].accel_energy + gyro_above;
                        float mapped = std::clamp((logf(e + 1e-6f) + 7.0f) / 7.0f, 0.0f, 1.0f);
                        cl_energy_sum[cl] += mapped;
                        cl_energy_count[cl]++;
                    }
                }
                for (int c = 0; c < num_clusters; c++) {
                    float mean_e = cl_energy_count[c] > 0
                                 ? cl_energy_sum[c] / cl_energy_count[c] : 0.0f;
                    float target = 5.0f + 175.0f * std::clamp(mean_e * 2.0f, 0.0f, 1.0f);
                    awr_cluster_bpm[c] = 0.99f * awr_cluster_bpm[c] + 0.01f * target;
                    awr_cluster_bpm[c] = std::clamp(awr_cluster_bpm[c], 5.0f, 180.0f);
                }
            } else if (energy_count > 0) {
                // Global BPM fallback when spatial is off
                float mean_energy = energy_sum / energy_count;
                float target_bpm = 5.0f + 175.0f * std::clamp(mean_energy * 2.0f, 0.0f, 1.0f);
                awr_group_bpm = 0.99f * awr_group_bpm + 0.01f * target_bpm;
                awr_group_bpm = std::clamp(awr_group_bpm, 5.0f, 180.0f);
            }
        }

        // =====================================================================
        // Second pass: TEMPORAL Kuramoto coupling + AGENTIVE energy propagation
        // =====================================================================

        // Kuramoto coupling: orbs synchronize phases within their spatial cluster
        // When spatial is off, all orbs couple globally. BRIDGE_AV needs this
        // so the bridge's firefly-breathe actually synchronises.
        if (awr_dim_active[AWR_DIM_TEMPORAL] || mode == MODE_BRIDGE_AV) {
            const float K = 0.0003f; // coupling strength (~5-10s convergence)
            float phase_nudge[max_orbs] = {};
            for (auto [k1, v1] : slot_map) {
                for (auto [k2, v2] : slot_map) {
                    if (v1 >= v2) continue;
                    // Only couple within same cluster (or all if spatial off)
                    if (awr_dim_active[AWR_DIM_SPATIAL] &&
                        (cluster_id[v1] < 0 || cluster_id[v1] != cluster_id[v2]))
                        continue;
                    float link = (float)sym[v1][v2] / 255.0f;
                    if (link < 0.1f) continue;
                    float dphi = sinf((awr_orbs[v2].firefly_phase - awr_orbs[v1].firefly_phase) * 2.0f * M_PI);
                    phase_nudge[v1] += K * dphi * link;
                    phase_nudge[v2] -= K * dphi * link;
                }
            }
            for (auto [k, v] : slot_map) {
                awr_orbs[v].firefly_phase += phase_nudge[v];
                awr_orbs[v].firefly_phase = fmodf(fmodf(awr_orbs[v].firefly_phase, 1.0f) + 1.0f, 1.0f);
            }
        }

        // Agentive: active orbs push energy to spatial neighbors
        if (awr_dim_active[AWR_DIM_AGENTIVE]) {
            for (auto [k, v] : slot_map)
                awr_orbs[v].agentive_received = 0.0f;

            for (auto [k, v] : slot_map) {
                float my_energy = awr_orbs[v].self_intensity;
                if (!awr_dim_active[AWR_DIM_SELF]) {
                    float gyro_above = std::max(0.0f, motion_features[v].gyro_energy - awr_orbs[v].gyro_baseline) * 0.001f;
                    float e = motion_features[v].accel_energy + gyro_above;
                    my_energy = std::clamp((logf(e + 1e-6f) + 7.0f) / 7.0f, 0.0f, 1.0f);
                }
                if (my_energy < 0.15f) continue;

                for (auto [k2, v2] : slot_map) {
                    if (v == v2) continue;
                    float link = (float)sym[v][v2] / 255.0f;
                    if (link < 0.3f) continue;
                    awr_orbs[v2].agentive_received += my_energy * link * 0.4f;
                }
            }
            for (auto [k, v] : slot_map)
                awr_orbs[v].agentive_received = std::clamp(awr_orbs[v].agentive_received, 0.0f, 1.0f);
        } else {
            for (auto [k, v] : slot_map)
                awr_orbs[v].agentive_received = 0.0f;
        }

        // =====================================================================
        // Third pass: Compose final colour and audio
        // =====================================================================
        for (auto [k, v] : slot_map) {
            auto& ao = awr_orbs[v];
            Eigen::Vector3f color;

            // --- HUE: spatial cluster or icosahedron vertex or warm white ---
            if (awr_dim_active[AWR_DIM_SPATIAL] && cluster_id[v] >= 0 && num_clusters > 0) {
                // Cluster hue via golden angle
                float hue_val = fmodf(cluster_id[v] * 0.618033988f, 1.0f);
                float h = hue_val * 6.0f;
                int hi = (int)h % 6;
                float f = h - floorf(h);
                float p = 0.0f, q = 1.0f - f, t = f; // S=1, V=1 — matches proximity mode
                switch (hi) {
                    case 0: color = {1.0f, t,    p};    break;
                    case 1: color = {q,    1.0f, p};    break;
                    case 2: color = {p,    1.0f, t};    break;
                    case 3: color = {p,    q,    1.0f}; break;
                    case 4: color = {t,    p,    1.0f}; break;
                    default:color = {1.0f, p,    q};    break;
                }
            } else if (awr_dim_active[AWR_DIM_SELF] && ao.gravity_vertex >= 0) {
                // No spatial → use icosahedron vertex color from gravity
                color = icosa_colors[ao.gravity_vertex];
                // Normalize to unit brightness so brightness channel controls it
                float cmax = color.maxCoeff();
                if (cmax > 0.01f) color /= cmax;
            } else {
                color = {0.9f, 0.75f, 0.5f}; // warm white
            }

            // --- BRIGHTNESS: composite of active dimensions ---
            float brightness;
            if (awr_active_count == 0) {
                brightness = 0.0f;
                color = {0.0f, 0.0f, 0.0f};
            } else {
                // Self: individual breathing pulse (smooth sine, 25%–100%)
                if (awr_dim_active[AWR_DIM_SELF]) {
                    brightness = 0.25f + 0.75f * (0.5f + 0.5f * sinf(ao.self_phase * 2.0f * M_PI));
                } else {
                    brightness = 0.5f;
                }

                // Temporal: group firefly flash (sharp cubed cosine, 0%–100%)
                if (awr_dim_active[AWR_DIM_TEMPORAL]) {
                    float pulse = powf(std::max(0.0f, cosf(ao.firefly_phase * 2.0f * M_PI)), 3.0f);
                    brightness *= pulse;
                }

                // Agentive: received energy boosts brightness
                brightness += ao.agentive_received * 0.3f;

                // Meta: low stability → subtle jitter
                if (awr_dim_active[AWR_DIM_METACOGNITIVE] && ao.meta_stability < 0.3f) {
                    float jitter = ((float)(loops % 7) / 7.0f - 0.5f) * 0.05f * (1.0f - ao.meta_stability / 0.3f);
                    brightness += jitter;
                }

                brightness = std::clamp(brightness, 0.0f, 1.0f);
            }

            Eigen::Vector3f out;
            if (brightness < 0.04f) {
                out = {0.0f, 0.0f, 0.0f}; // true black — avoids RGB565 green tint
            } else {
                out = color * brightness;
                out = out.cwiseMin(1.0f).cwiseMax(0.0f);
            }
            s_packet.led_colour[v] = rgb_to_565(out.x(), out.y(), out.z());
            ao.color = out;

            // --- Audio: bleep on firefly phase zero-crossing ---
            uint8_t note = 0, period = 0;
            if (awr_dim_active[AWR_DIM_TEMPORAL] && ao.fired_this_cycle) {
                // Note from cluster membership or default
                if (cluster_id[v] >= 0)
                    note = std::min(3, (cluster_id[v] % 3) + 1);
                else
                    note = 1; // default C
                period = 5; // one-shot: play once then server clears next frame
            }
            // Agentive sonic ripple: neighbors that receive energy bleep quietly
            if (awr_dim_active[AWR_DIM_AGENTIVE] && ao.agentive_received > 0.3f && note == 0) {
                note = (cluster_id[v] >= 0) ? std::min(3, (cluster_id[v] % 3) + 1) : 1;
                period = (uint8_t)std::max(10, (int)(50.0f - 40.0f * ao.agentive_received));
            }
            audio_notes[v]   = note;
            audio_periods[v] = period;
        }

        // --- JSON export ---
        json j;
        for (auto [k, v] : slot_map) {
            auto& ao = awr_orbs[v];
            j[fmt::format("{:06x}", k)] = {
                {"accel_x", rdata[v].accel_x},
                {"accel_y", rdata[v].accel_y},
                {"accel_z", rdata[v].accel_z},
                {"gyro_x", rdata[v].gyro_x},
                {"gyro_y", rdata[v].gyro_y},
                {"gyro_z", rdata[v].gyro_z},
                {"batt_v", rdata[v].batt_v},
                {"batt_i", rdata[v].batt_i},
                {"rssi", rdata[v].rssi},
                {"pkt_latency", (int)rdata[v].pkt_latency},
                {"missed", slot_watchdog[v] < slot_watchdog_limit - 1},
                {"miss_rate", orb_miss_rate[v]},
                {"firmware", fmt::format("{}.{}", (rdata[v].revision >> 8) & 0xff, rdata[v].revision & 0xff)},
                // Top-level cluster + a handful of awareness-derived values
                // that downstream AV consumers (the python bridge) want to
                // map into light params. Still duplicated in the awareness
                // sub-object below for existing readers.
                {"cluster", cluster_id[v]},
                {"hue", cluster_id[v] >= 0
                        ? fmodf(cluster_id[v] * 0.618033988f, 1.0f)
                        : 0.0f},
                {"final_brightness", ao.color.maxCoeff()},
                {"alignment", ao.gravity_brightness},
                {"firefly_phase", ao.firefly_phase},
                {"bpm", ao.self_bpm},
                {"diag_flags", [&]() {
                    uint32_t raw; memcpy(&raw, &rdata[v].temperature, 4);
                    return (raw >> 16) & 0xFF;
                }()},
                {"awareness", {
                    {"self_intensity", ao.self_intensity},
                    {"self_phase", ao.self_phase},
                    {"self_bpm", ao.self_bpm},
                    {"gravity_vertex", ao.gravity_vertex},
                    {"firefly_phase", ao.firefly_phase},
                    {"fired", ao.fired_this_cycle},
                    {"agentive_received", ao.agentive_received},
                    {"meta_stability", ao.meta_stability},
                    {"meta_tenure", ao.meta_tenure},
                    {"cluster", cluster_id[v]},
                }},
                {"features", {
                    {"accel_energy", motion_features[v].accel_energy},
                    {"gyro_energy", motion_features[v].gyro_energy},
                    {"is_moving", motion_features[v].is_moving},
                }},
            };
        }

        // Active dimensions bitmask and names
        json dim_states = json::array();
        for (int d = 0; d < AWR_NUM_DIMS; d++)
            dim_states.push_back({{"name", awr_dim_names[d]}, {"active", awr_dim_active[d]}});

        json cluster_bpms = json::array();
        for (int c = 0; c < num_clusters; c++)
            cluster_bpms.push_back(awr_cluster_bpm[c]);

        // Headline prompt for the display surface (null when nothing active).
        // Operator cycles with ] / [ / c in the TUI. Emitted here in the
        // awareness writer so MODE_BRIDGE_AV (which reuses this path) picks
        // it up automatically.
        json current_prompt_json = nullptr;
        if (current_prompt_idx >= 0 && current_prompt_idx < NUM_PROMPTS) {
            current_prompt_json = prompts[current_prompt_idx];
        }

        j["awareness_mode"] = {
            {"dimensions", dim_states},
            {"active_count", awr_active_count},
            {"num_clusters", num_clusters},
            {"group_bpm", awr_group_bpm},
            {"cluster_bpms", cluster_bpms},
        };

        // Full NxN proximity matrix (same format as proximity mode)
        json pm;
        for (auto [k1, v1] : slot_map) {
            json row;
            for (auto [k2, v2] : slot_map)
                row[fmt::format("{}", v2)] = sym[v1][v2];
            pm[fmt::format("{}", v1)] = row;
        }
        j["proximity_matrix"] = pm;

        // Slot-to-serial mapping
        json sm;
        for (auto [k, v] : slot_map)
            sm[fmt::format("{}", v)] = fmt::format("{:06x}", k);
        j["slot_to_serial"] = sm;

        j["mode"] = mode_names[mode];
        j["current_prompt"] = current_prompt_json;
        j["network_stats"] = {
            {"miss_ratio", missed_packet_ratio},
            {"rate_hz", live_hz},
            {"activity", fleet_activity},
            {"total_missed_pkts", total_missed_pkts},
            {"num_orbs", (int)slot_map.size()},
            {"frame_num", loops},
            {"jitter_bins", bins},
        };

        // Atomic write
        std::string data = j.dump(4) + "\n";
        int fd = open("/tmp/orb_data_staging", O_CREAT | O_WRONLY | O_TRUNC, 0644);
        write(fd, data.c_str(), data.size());
        fsync(fd);
        close(fd);
        int dirfd = open("/tmp", O_DIRECTORY | O_RDONLY);
        fsync(dirfd);
        close(dirfd);
        rename("/tmp/orb_data_staging", "/tmp/orb_data");
    }
    // ===================== End Awareness Mode =====================

    // ------------------------------------------------------------------------
    // Main send loop, multicast packets go out at 50Hz. Always incrementing the
    // next_time var by the period ensures wakeup is properly synchronous and won't drift
    void run_send()
    {

        auto next_time  = steady_clock::now();
        next_time_us    = duration_cast<microseconds>(next_time.time_since_epoch()).count();
        slots_allocated = 0;
        // Wall-clock boot time (epoch ms) so poll_prompt_cmd() ignores stale
        // command files written before this server started.
        server_start_ms = duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
        while (running)
        {
            // Run the packet sent. This should happen first, so it is most accurately timed
            execute_send_task();
            // Update the user interface. This happens here, rather than on receive packet, because
            // this function is executed at a regular 50Hz, whereas that receive callback can
            // happen many or no times per 50Hz cycle
            update_tui();   
            int64_t current_time_us = duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count();
            elapsed_us      = current_time_us - next_time_us;
            loops++;
            if (g_autorate) {
                // Closed-loop: hold missed_packet_ratio near g_autorate_tgt.
                // Only act every AUTORATE_DWELL_US so each change can show up in
                // the (slow EWMA) miss signal before the next one — this dwell,
                // plus the deadband below, is the hysteresis that stops it from
                // skipping rates wildly.
                if (current_time_us - last_autorate_us >= AUTORATE_DWELL_US) {
                    last_autorate_us = current_time_us;
                    double hz = live_hz;
                    if (missed_packet_ratio > g_autorate_tgt + AUTORATE_DOWN_BAND) {
                        // Dropping packets: back off fast (multiplicative).
                        hz *= AUTORATE_DOWN_MULT;
                    } else if (missed_packet_ratio < g_autorate_tgt - AUTORATE_UP_BAND) {
                        // Headroom: creep up slowly — even mid-session with
                        // orbs in hand. The old fleet-at-rest gate is gone
                        // (see AUTORATE_UP_BAND comment); a wrong step up
                        // self-corrects via the DOWN_BAND leg within a dwell.
                        hz += AUTORATE_UP_STEP;
                    }
                    // else: inside the 0.09..0.12 hysteresis band — hold.
                    hz = std::min(g_rate_hz, std::max(g_armin_hz, hz));
                    live_hz = hz;
                    period  = duration_cast<nanoseconds>(duration<double>(1.0 / hz));
                }
            } else if (g_adaptive) {
                // slow the loop as orbs join so the round-trip budget (orbs*Hz)
                // stays fixed => every orb keeps a consistent update rate.
                int n = __builtin_popcount(slots_allocated);
                double hz = (n <= 0) ? g_rate_hz
                          : std::min(g_rate_hz, std::max(g_rmin_hz, g_budget_rt / n));
                live_hz = hz;
                period  = duration_cast<nanoseconds>(duration<double>(1.0 / hz));
            }
            next_time       += period;
            next_time_us    = duration_cast<microseconds>(next_time.time_since_epoch()).count();
            std::this_thread::sleep_until(next_time);
        }
    }
    void execute_send_task()
    {
        auto now    = steady_clock::now();
        now_us      = duration_cast<microseconds>(now.time_since_epoch()).count();
        auto delta  = now_us - next_time_us;

        int idx     = delta / 10 + 10;
        idx         = idx < 0 ? 0 : idx > 99 ? 99 : idx;
        bins[idx]++;


        s_packet.packet_num     = loops;

        // Apply any pending external prompt-select command (session controller).
        poll_prompt_cmd();
        poll_flash_cmd();
        poll_glow_cmd();

        // Command selection: awareness-spatial, proximity, and bridge-AV all
        // need ESP-NOW so orbs broadcast their RSSI top-6 and the server can
        // build rssi_matrix for clustering. Without this, bridge-AV fans
        // every orb into its own voice because clustering has no input data.
        if (mode == MODE_AWARENESS) {
            if (awr_dim_active[AWR_DIM_SPATIAL])
                s_packet.command = CMD_PROX_ON;
            else
                s_packet.command = CMD_PROX_OFF;
        } else if (mode == MODE_BRIDGE_AV) {
            s_packet.command = CMD_PROX_ON;
        } else {
            s_packet.command = (mode == MODE_PROXIMITY) ? CMD_PROX_ON : CMD_PROX_OFF;
        }

        // Per-orb motion (accel_dev[], fast_active[]) + fleet_activity, every frame
        // in every mode. Runs BEFORE decay + clustering so those see current-frame
        // motion (was previously at end of frame => one frame stale at low Hz).
        update_fleet_activity();

        // Decay the RSSI proximity matrix once per frame whenever proximity is
        // active, so links reflect CURRENT proximity rather than the strongest
        // ever seen (see decay_rssi_matrix()).
        if (s_packet.command == CMD_PROX_ON)
            decay_rssi_matrix();

        // Clear padding (used for audio commands in cluster/awareness modes)
        memset(s_packet.padding, 0, sizeof(s_packet.padding));

        // Generate led_colours based on current mode
        if (mode == MODE_CLUSTER)
            generate_cluster_colours();
        else if (mode == MODE_PROXIMITY)
            generate_proximity_colours();
        else if (mode == MODE_AWARENESS)
            generate_awareness_colours();
        else if (mode == MODE_BRIDGE_AV) {
            // Re-use the AWARENESS pipeline so /tmp/orb_data carries
            // cluster + firefly + bpm, then replace the LEDs with whatever
            // the bridge has latched (or heartbeat fallback if silent).
            generate_awareness_colours();
            apply_bridge_colours();
        }
        else if (mode == MODE_COMMS)
            memset(s_packet.led_colour, 0, sizeof(s_packet.led_colour));
        else
            generate_led_colours();

        // The packet contains a field for allocated slots, we cycle through them,
        // so any orb will always see their allocation within 32/50th of a second
        //
        // We get the next serial from the queue, check if it is still allocated,
        // then send if so, also returning it to back of queue. If no longer allocated,
        // drop the entry so no longer on the queue.
        s_packet.slot_alloc = 0;
        for(int i = 0; i < slot_queue.size(); i++)
        {
            uint32_t s = slot_queue.front();
            slot_queue.pop_front();
            if (slot_map.count(s))
            {
                // This entry is present in the map, so send, and put back on queue
                s_packet.slot_alloc = s | (slot_map[s] << 24);
                slot_queue.push_back(s);
                break;
            }
        }
        // If we don't find a mapped entry, or there are no entries, the slot allocation
        // will be zero, which will not match any orb.
    
        // OTA Command
        // If an ota reuest has been made, we set the command to ota and wait for a responce
        if (run_ota)
        {
            s_packet.command = (ota_serial << 8) | CMD_OTA;
            wprintw(win, "Sending OTA to %06x frame:%d\n", ota_serial, run_ota_loops);
            run_ota_loops++;
            if (run_ota_loops > run_ota_limit)
            {
                run_ota = false;
            }
        }

        // Sleep command — overrides other commands for the target orb.
        // When a fleet-sleep queue is pending, drain it one serial at a time:
        // each target gets SLEEP for 25 frames (~0.5 s unicast, ACK'd) before
        // the next is loaded, so 24 orbs go down in ~12 s.
        if (sleep_frames <= 0 && !sleep_queue.empty() && !run_ota)
        {
            sleep_serial = sleep_queue.front();
            sleep_queue.pop_front();
            sleep_frames = 25;
            wprintw(win, "Sleeping orb %06x (%zu more queued)\n",
                    sleep_serial, sleep_queue.size());
        }
        if (sleep_frames > 0 && !run_ota)
        {
            s_packet.command = (sleep_serial << 8) | CMD_SLEEP;
            if (--sleep_frames <= 0) sleep_serial = 0;
        }

        // Override the LED colours to white, if the 's' (show) key has been pressed
        if (show_orb >= 0)
        {
            s_packet.led_colour[show_orb] = 0xffff;
        }

        // Finale light coupling: glow scales each slot's computed colour by
        // its level (the conductor holds idle orbs at half brightness and
        // opens to full with shake/spin). A chime flash holds the slot at
        // FULL computed colour for its frames — a brightness peak in the
        // orb's own colour, never white, so colour identity is preserved.
        if (glow_ttl > 0) glow_ttl--;
        for (int i = 0; i < max_orbs && i < 32; i++)
        {
            if (flash_frames[i] > 0)
            {
                // Jump envelope: FULL colour, a deep 5% dip, then the glow
                // floor resumes — max -> 5% -> 25%, unmissable but never dark.
                flash_frames[i]--;
                if (flash_frames[i] < 6)
                {
                    uint16_t c = s_packet.led_colour[i];
                    int r = ((c >> 11) & 0x1f) / 20;
                    int g = ((c >> 5) & 0x3f) / 20;
                    int b = (c & 0x1f) / 20;
                    s_packet.led_colour[i] = (uint16_t)((r << 11) | (g << 5) | b);
                }
                continue;               // full colour otherwise (skip glow dim)
            }
            if (glow_ttl <= 0) continue;
            int lv = glow_level[i];
            if (lv >= 255) continue;
            uint16_t c = s_packet.led_colour[i];
            int r = ((c >> 11) & 0x1f) * lv / 255;
            int g = ((c >> 5) & 0x3f) * lv / 255;
            int b = (c & 0x1f) * lv / 255;
            s_packet.led_colour[i] = (uint16_t)((r << 11) | (g << 5) | b);
        }

        // Send the 128-byte fan-out. In unicast mode each known orb gets its
        // own copy (which the AP ACKs/retries like any unicast frame) and the
        // multicast only goes out periodically for discovery — at the lowest
        // basic rate it costs real airtime, and new orbs join within ~200ms.
        // Orbs re-arm their TX-alignment timer on every RX, so receiving both
        // copies in one frame is harmless.
        if (!unicast_mode || loops % discovery_mcast_interval == 0)
        {
            ssize_t sent = sendto(
                sock_send,
                &s_packet,
                spacket_size,
                0,
                reinterpret_cast<sockaddr *>(&dest_addr),
                sizeof(dest_addr));
            if (sent < 0)
            {
                perror("sendto");
            }
        }
        if (unicast_mode)
        {
            sockaddr_in ucast_addr = dest_addr;   // same port (5000), per-orb IP
            uint32_t s = slots_allocated;
            for (int i = 0; i < max_orbs; i++, s >>= 1)
            {
                uint32_t a = orb_addr[i].load(std::memory_order_relaxed);
                if (!(s & 1) || a == 0) continue;
                ucast_addr.sin_addr.s_addr = a;
                if (sendto(sock_send, &s_packet, spacket_size, 0,
                           reinterpret_cast<sockaddr *>(&ucast_addr),
                           sizeof(ucast_addr)) < 0)
                {
                    perror("sendto unicast");
                }
            }

            // Discovery probe: one DHCP-pool address per frame (see
            // probe_pool_* above). Skip addresses already being served.
            static int probe_idx = 0;
            uint32_t probe_ip = htonl((10u << 24) | (probe_pool_first + probe_idx));
            probe_idx = (probe_idx + 1) % probe_pool_size;
            bool active = false;
            uint32_t s2 = slots_allocated;
            for (int i = 0; i < max_orbs; i++, s2 >>= 1)
                if ((s2 & 1) && orb_addr[i].load(std::memory_order_relaxed) == probe_ip)
                {
                    active = true;
                    break;
                }
            if (!active)
            {
                ucast_addr.sin_addr.s_addr = probe_ip;
                // Best-effort: most pool addresses are unleased and the ARP
                // lookup just expires — no perror spam for those.
                sendto(sock_send, &s_packet, spacket_size, 0,
                       reinterpret_cast<sockaddr *>(&ucast_addr),
                       sizeof(ucast_addr));
            }
        }

        // Process slot watchdog expire
        for(int i = 0; i < 32; i++)
        {
            if (--slot_watchdog[i] <= 0)
            {
                slot_watchdog[i] = 0;
                // This slot has not been tickled for a while, clear the allocated bit..
                slots_allocated &= ~(1 << i);
                // Reset charging/eligibility state so the next orb to take this
                // slot starts fresh (done here, in the send thread, to stay
                // race-free with the eligibility loop that reads these).
                charge_run[i] = 0; discharge_run[i] = 0;
                orb_charging[i] = false; orb_eligible[i] = false;
                wave_lift_us[i] = 0;
                accel_seen[i] = false;   // re-seed activity baseline for next orb
                accel_dev[i] = 0; orb_miss_rate[i] = 0; move_until_us[i] = 0;
                fast_active[i] = false; meas_armed[i] = false; meas_was_quiet[i] = false;
                // Clear the last received packet too. Otherwise a departed orb's
                // stale row (non-zero serial, rssi, batt) keeps rendering in the
                // TUI and the COMMS-mode scrapers (net_capture_tui / count_sweep)
                // count it as still-active — the "allocated, not heard" miscount.
                // Safe: the JSON/cluster/awareness paths only read rdata[v] for v
                // in slot_map, and this slot is being removed from the map below.
                rdata[i] = Recv_packet{};
                // .. and remove from the map
                for(auto [k,v] : slot_map)
                    if (v == i)
                    {
                        slot_map.erase(k);
                        break;
                    }
            }
        }

        // Find total missed packets in valid slots
        total_missed_pkts = 0;
        for(auto [k,v] : slot_map)
        {
            total_missed_pkts += slot_watchdog[v] < slot_watchdog_limit - 1;
        }
        // Do a bit of filtering on the miss ratio
        float r = 0;
        if (slot_map.size()) 
            r = (float)total_missed_pkts / slot_map.size();
        const float fconst = 0.99;
        missed_packet_ratio = fconst * missed_packet_ratio + (1 - fconst) * r;

        // Per-orb miss rate: the aggregate above hides whether SOME orbs starve
        // (weak rssi / distant / low battery) while others are fine. Time-based
        // EWMA so the per-orb signal isn't itself rate-biased (same fix class as
        // the latency terms). Reset on slot teardown.
        float miss_tau = prompt_settings.get("GLOBAL", "miss_ewma_tau", 3.0f);
        float ma = (miss_tau > 0.0f) ? expf(-(1.0f / (float)live_hz) / miss_tau) : 0.99f;
        for (auto [k, v] : slot_map) {
            float m = (slot_watchdog[v] < slot_watchdog_limit - 1) ? 1.0f : 0.0f;
            orb_miss_rate[v] = ma * orb_miss_rate[v] + (1.0f - ma) * m;
        }
    }

    // Cheap, mode-independent motion detector for --autorate's recovery gate.
    // Per-slot per-axis EMA baseline; the fleet signal is an EWMA of the largest
    // per-orb deviation of the accel vector from its baseline (in g). At rest the
    // gravity vector is constant => ~0; picking an orb up rotates/jolts it =>
    // spike. Runs every frame in every mode (unlike the cluster motion pipeline).
    void update_fleet_activity()
    {
        // Fast-path settings (motion-aware cluster reassignment). Cheap to read each
        // frame; prompt_settings is cached + hot-reloaded on the send thread.
        bool  fast_on  = prompt_settings.get("GLOBAL", "fast_path_enable", 0.0f) >= 0.5f;
        float fast_g   = prompt_settings.get("GLOBAL", "fast_move_g", 0.15f);
        int   fast_win = (int)prompt_settings.get("GLOBAL", "fast_window_ms", 500.0f);

        float peak = 0;
        for (auto [k, v] : slot_map)
        {
            float ax = rdata[v].accel_x, ay = rdata[v].accel_y, az = rdata[v].accel_z;
            if (!accel_seen[v]) {           // seed baseline so a fresh slot doesn't
                accel_base[v][0] = ax;      // read as a ~1g spike on its first frame
                accel_base[v][1] = ay;
                accel_base[v][2] = az;
                accel_seen[v] = true;
                accel_dev[v] = 0.0f;
                continue;
            }
            float dx = ax - accel_base[v][0];
            float dy = ay - accel_base[v][1];
            float dz = az - accel_base[v][2];
            float dev = sqrtf(dx*dx + dy*dy + dz*dz);
            accel_dev[v] = dev;             // per-orb signal for fast-path + measurement
            if (dev > peak) peak = dev;
            // Motion fast-path latch: while an orb is being handled (and briefly
            // after), its cluster debounce is shortened so it re-clusters quickly.
            if (fast_on && dev > fast_g)
                move_until_us[v] = now_us + (int64_t)fast_win * 1000;
            fast_active[v] = fast_on && (now_us < move_until_us[v]);
            const float b = 0.05f;          // baseline tracks new resting pose in ~1s
            accel_base[v][0] += b * dx;
            accel_base[v][1] += b * dy;
            accel_base[v][2] += b * dz;
        }
        fleet_activity = 0.97f * fleet_activity + 0.03f * peak;  // ~1-2s decay
    }



    void run_recv()
    {
        socklen_t src_len = sizeof(src_addr);
        wprintw(win, "Starting receive thread, listening on port %d...\n", port);
        
        for(int i = 0; i < 32; slot_watchdog[i++] = 0);
        while (running)
        {
            
            ssize_t n = recvfrom(
                sock_recv, 
                message_recv, 
                sizeof(message_recv),  // Leave room for null terminator
                0,
                reinterpret_cast<sockaddr *>(&src_addr), 
                &src_len
            );
            
            // Accept the 64-byte head + 0..PROX_MAX proximity pairs (64..96 bytes:
            // covers 64B legacy, 76B top-6 fleet, and top-N ablation builds).
            // Still excludes our own 128-byte multicast sends.
            if (n >= 64 && n <= 64 + 2 * PROX_MAX)
            {
                int64_t rtime_us    = duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count();
                last_recv_us.store(rtime_us, std::memory_order_relaxed);
                
                // Get sender IP as string
                char sender_ip[INET_ADDRSTRLEN];
                inet_ntop(AF_INET, &src_addr.sin_addr, sender_ip, INET_ADDRSTRLEN);
                
                // -----------------------------------------------------
                // Interpret as packet structure
                Recv_packet *r_pkt = (Recv_packet*)message_recv;

                uint32_t serial = r_pkt->slot_alloc & 0xffffff;

                // wprintw(win, "Checking %06x for map\n", serial);
                if (slot_map.count(serial) == 0)
                {
                    // This Orb is not in the map, find the lowest unoccupied slot
                    uint32_t s = slots_allocated;
                    int alc = -1;
                    for(int i = 0; i < 32; i++, s >>= 1)
                        if (!(s & 1))
                        {
                            // This slot is free
                            slot_map[serial]    = i;
                            slot_watchdog[i]    = slot_watchdog_limit;
                            slots_allocated     |= 1 << i;
                            alc                 = i;
                            break;
                        }
                    // Put the serial on the queue, so that it circulates for transmission
                    // of the slot allocation
                    slot_queue.push_back(serial);
                    wprintw(win, "No alloc, %06x given %d\n", serial, alc);
                }
                else
                {
                    // This orb is in the map, tickle the watchdog
                    slot_watchdog[slot_map[serial]] = slot_watchdog_limit;
                }
                // At this point, the map will have an entry for this orb, so place the data
                // in the main table
                int slot                = slot_map[serial];
                orb_addr[slot].store(src_addr.sin_addr.s_addr, std::memory_order_relaxed);
                rdata[slot]             = *r_pkt;

                // How many proximity pairs this orb actually reported (after the
                // 64-byte head): legacy 64B => 0, top-6 fleet 76B => 6, ablation
                // builds up to PROX_MAX. Zero the rest so stale bytes from a longer
                // prior packet in the shared recv buffer can't leak in.
                int n_prox = (int)((n - 64) / (ssize_t)sizeof(proximity_entry_t));
                if (n_prox < 0) n_prox = 0;
                if (n_prox > PROX_MAX) n_prox = PROX_MAX;
                for (int i = n_prox; i < PROX_MAX; i++)
                    rdata[slot].rssi_proximity[i] = proximity_entry_t{0, 0};

                // Populate NxN RSSI proximity matrix from this orb's top-N report
                for (int i = 0; i < n_prox; i++) {
                    uint8_t peer = rdata[slot].rssi_proximity[i].slot_id;
                    uint8_t str  = rdata[slot].rssi_proximity[i].strength;
                    if (str > 0 && peer < max_orbs) {
                        rssi_matrix[slot][peer] = str;
                    }
                }

                // Adjust acceleration data. FIXME!! This really should be done downstream
                // but so much stuff reads the acceleration info directly, we will do this
                // here for now.
                //
                // We need to apply a rotation to the XY vectors, because the LED frame is 
                // not quite aligned with the LSM6DSM
                Eigen::Vector3f av = {rdata[slot].accel_x, rdata[slot].accel_y, -rdata[slot].accel_z};
                Eigen::AngleAxisf rot(0.32, Eigen::Vector3f::UnitZ());
                av = rot * av;
                rdata[slot].accel_x = av[0];
                rdata[slot].accel_y = av[1];
                rdata[slot].accel_z = av[2];


                odata[slot].rp          = rdata + slot;
                odata[slot].rlatency    = rtime_us - now_us;

                if (run_ota)
                {
                    // We are trying to run OTA, look for ack from device
                    //FIXME!!! Should this be ff? may interfere with slotless
                    if (serial == ota_serial && (r_pkt->revision & 0xff000000) == 0xff000000)
                    {
                        wprintw(win, "Device %06x ack'd, is now doing OTA\n", serial);
                        run_ota = false;
                    }
                }
                // std::string pstr = r_pkt->str();
                // printf("%15s:%4d %s %08x\n", sender_ip, ntohs(src_addr.sin_port), pstr.c_str(), slots_allocated);
            } 
            else if (n < 0) 
            {
                if (running) 
                {  // Only print error if we're still supposed to be running
                    perror("recvfrom error");
                }
            }
        }
        wprintw(win, "Receive thread ending\n");
    }

    // ----- MODE_BRIDGE_AV colour ingress -----
    // Packet format (tentative v1):
    //   magic:   4 bytes  "ORB2"
    //   count:   1 byte
    //   entries × count (6 bytes each):
    //     slot_id:  1 byte  (0..31)
    //     r, g, b:  1 byte each
    //     ttl_ms:   2 bytes big-endian
    // Bridge and server run on the same PC so the 127.0.0.1:5002 socket is
    // loopback-only — no wireless traffic.
    void run_bridge_listen()
    {
        uint8_t buf[1500];
        wprintw(win, "Bridge listener up on 127.0.0.1:%d\n", bridge_port);
        while (running) {
            ssize_t n = recvfrom(sock_bridge, buf, sizeof(buf), 0, nullptr, nullptr);
            if (n <= 0) {
                if (running) { /* EINTR or shutdown — keep looping */ }
                continue;
            }
            if (n < 5) continue;
            if (!(buf[0] == 'O' && buf[1] == 'R' && buf[2] == 'B' && buf[3] == '2')) continue;
            int count = buf[4];
            if (count < 0 || count > max_orbs) continue;
            if (n < 5 + 6 * count) continue;

            int64_t now = duration_cast<microseconds>(
                steady_clock::now().time_since_epoch()).count();

            std::lock_guard<std::mutex> lk(bridge_colours_mutex);
            for (int i = 0; i < count; i++) {
                uint8_t *p = buf + 5 + 6 * i;
                int slot = p[0];
                if (slot < 0 || slot >= max_orbs) continue;
                uint16_t ttl_ms = (uint16_t(p[4]) << 8) | p[5];
                bridge_colours[slot].r = p[1];
                bridge_colours[slot].g = p[2];
                bridge_colours[slot].b = p[3];
                bridge_colours[slot].expiry_us = now + int64_t(ttl_ms) * 1000;
            }
        }
        wprintw(win, "Bridge listener ending\n");
    }

    // Overwrite s_packet.led_colour[] for each known slot from the bridge latch.
    // When a slot's latest bridge packet has expired, fall back to a slow dim
    // blue heartbeat so the orb is visibly alive ("server up, bridge silent").
    void apply_bridge_colours()
    {
        int64_t now = duration_cast<microseconds>(
            steady_clock::now().time_since_epoch()).count();
        // Heartbeat phase: 3-second cycle; brightness 0.1..0.35
        float phase = float((loops % 150)) / 150.0f;
        float hb = 0.1f + 0.25f * (0.5f * (1.0f - cosf(phase * 2.0f * 3.14159265f)));
        uint16_t hb_rgb = rgb_to_565(0.0f, 0.0f, hb);

        std::lock_guard<std::mutex> lk(bridge_colours_mutex);
        for (auto [k, v] : slot_map) {
            BridgeColour &bc = bridge_colours[v];
            if (bc.expiry_us > now) {
                float r = bc.r / 255.0f, g = bc.g / 255.0f, b = bc.b / 255.0f;
                s_packet.led_colour[v] = rgb_to_565(r, g, b);
            } else {
                s_packet.led_colour[v] = hb_rgb;
            }
        }
    }

    void update_tui()
    {
        // Update display, capture ctrl-c, handle other interactions.

        // Timeout the show orb signal (once per frame)
        if (show_orb_timeout <= 0)
            show_orb = -1;
        else
            show_orb_timeout--;

        // Drain ALL buffered keypresses this frame. At low loop rates (autorate
        // can floor the loop near 10Hz) the terminal's key-repeat arrives faster
        // than one-getch-per-frame can consume it, so held arrow keys lag and
        // then "auto-scroll" after release as the backlog drains. Consuming the
        // whole buffer each frame eliminates the backlog (input is still polled
        // at the loop rate, but never piles up).
        int ch;
        while ((ch = getch()) != ERR)
        {
            // Quit is ESC ONLY — 'q' now sleeps quiescent orbs (below). The
            // dock's stop-server relay sends Escape (launch.sh).
            if (ch == '\x1b')
            {
                running = false;
            }
            if (ch == 'a' || ch == 'q')
            {
                // 'a': queue EVERY reporting orb for sleep.
                // 'q': only quiescent orbs — at rest (|accel|≈1 g, low gyro),
                //      i.e. parked on the table, not in someone's hands.
                // Light sleep with accel wake: a shake brings any orb back.
                int queued = 0;
                for (int i = 0; i < max_orbs; i++)
                {
                    uint32_t ser = rdata[i].slot_alloc;
                    uint32_t target = ser & 0xffffff;
                    if ((ser >> 24) == 0xff || target == 0) continue;
                    if (ch == 'q')
                    {
                        float ax = rdata[i].accel_x, ay = rdata[i].accel_y,
                              az = rdata[i].accel_z;
                        float gx = rdata[i].gyro_x, gy = rdata[i].gyro_y,
                              gz = rdata[i].gyro_z;
                        float amag = sqrtf(ax * ax + ay * ay + az * az);
                        float gmag = sqrtf(gx * gx + gy * gy + gz * gz);
                        if (fabsf(amag - 1.0f) > 0.06f || gmag > 30.0f)
                            continue;   // being handled — leave it awake
                    }
                    sleep_queue.push_back(target);
                    queued++;
                }
                wprintw(win, "%s: %d orb%s queued for sleep\n",
                        ch == 'a' ? "Sleep ALL" : "Sleep quiescent",
                        queued, queued == 1 ? "" : "s");
            }
            if (ch == 't') {
                print_threshold_test();
            }
            // Manual prompt cycling for the collective-experience loop.
            if (ch == ']') {
                current_prompt_idx = (current_prompt_idx + 1) % NUM_PROMPTS;
                wprintw(win, "Prompt -> %s\n", prompts[current_prompt_idx]);
            }
            if (ch == '[') {
                current_prompt_idx = (current_prompt_idx + NUM_PROMPTS - 1) % NUM_PROMPTS;
                if (current_prompt_idx < 0) current_prompt_idx = NUM_PROMPTS - 1;
                wprintw(win, "Prompt -> %s\n", prompts[current_prompt_idx]);
            }
            if (ch == 'c') {
                current_prompt_idx = -1;
                wprintw(win, "Prompt cleared\n");
            }
            if (ch == KEY_UP && cursor_line > 0)                cursor_line--;
            if (ch == KEY_DOWN && cursor_line < max_orbs - 1)   cursor_line++;
            if (ch == 'p')
            {
                uint32_t serial = rdata[cursor_line].slot_alloc;
                if ((serial >> 24) != 0xff && (serial & 0xffffff) != 0)
                {
                    // a valid device to try updating
                    serial &= 0xffffff;
                    endwin();
                    printf("\nOTA Update of device %06x? (type 'yes' to proceed) ", serial);
                    char *line = 0;
                    size_t len = 0;
                    ssize_t read = getline(&line, &len, stdin);
                    if (read != -1 && !strcmp(line, "yes\n"))
                    {
                        // Trigger OTA
                        wprintw(win, "Running OTA on %06x\n", serial);
                        run_ota = true;
                        run_ota_loops = 0;
                        ota_serial = serial;
                    }
                }
            }
            if (ch == 's')
            {
                // show current orb
                show_orb = cursor_line;
                // Half sec indicator
                show_orb_timeout = 25;
            }
            if (ch == 'z')
            {
                // Sleep the orb under cursor
                uint32_t ser = rdata[cursor_line].slot_alloc;
                uint32_t target_serial = ser & 0xffffff;
                if ((ser >> 24) != 0xff && target_serial != 0) {
                    sleep_serial = target_serial;
                    sleep_frames = 50; // send for 1 second to ensure delivery
                    wprintw(win, "Sleeping orb %06x\n", target_serial);
                }
            }
            // Feedback framing toggle (study manipulation, ethics ref 33302).
            if (ch == 'f') {
                framing_collective = !framing_collective;
                wprintw(win, "Framing: %s\n",
                        framing_collective ? "COLLECTIVE (whole-room)"
                                           : "INDIVIDUAL (own-orb)");
            }
            if (ch == 'm')
            {
                mode = (mode + 1) % NUM_MODES;
                if (mode == MODE_CLUSTER) cluster_state.initialized = false;
                if (mode == MODE_PROXIMITY) memset(rssi_matrix, 0, sizeof(rssi_matrix));
                if (mode == MODE_AWARENESS) {
                    memset(rssi_matrix, 0, sizeof(rssi_matrix));
                    for (int i = 0; i < max_orbs; i++) awr_orbs[i] = AwarenessOrb{};
                    // Start with all dims off (baseline)
                    for (int d = 0; d < AWR_NUM_DIMS; d++) awr_dim_active[d] = false;
                    wprintw(win, "  Keys 1-5 toggle: 1=SELF 2=SPATIAL 3=TEMPORAL 4=AGENTIVE 5=META\n");
                }
                wprintw(win, "Mode: %s\n", mode_names[mode]);
            }
            // Awareness dimension toggles (1-5) — only active in awareness mode
            if (mode == MODE_AWARENESS && ch >= '1' && ch <= '5') {
                int dim = ch - '1';
                awr_dim_active[dim] = !awr_dim_active[dim];
                wprintw(win, "  %s: %s\n", awr_dim_names[dim],
                        awr_dim_active[dim] ? "ON" : "OFF");
            }
            // if (ch == 'g')
            // {
            //     if (graph_pid == -1)
            //     {
            //         char *const argv[] = {"", NULL};
            //         graph_pid = spawn_script("../scripts/graph", argv);
            //         // wprintw(win, "PID is %d\n", graph_pid);
            //     }
            //     else
            //     {
            //         kill(-graph_pid, SIGTERM);
            //         graph_pid = -1;
            //     }
            // }
        }

        // Status line per slot
        const char* prompt_label = (current_prompt_idx >= 0 && current_prompt_idx < NUM_PROMPTS)
            ? prompts[current_prompt_idx] : "—";
        if (mode == MODE_AWARENESS) {
            // Show dimension toggles in command line
            mvprintw(cmd_line, 0, "%s  [%s]  1:%s 2:%s 3:%s 4:%s 5:%s  ][:%-12s ",
                commands, mode_names[mode],
                awr_dim_active[0] ? "SELF"  : "self",
                awr_dim_active[1] ? "SPAT"  : "spat",
                awr_dim_active[2] ? "TEMP"  : "temp",
                awr_dim_active[3] ? "AGNT"  : "agnt",
                awr_dim_active[4] ? "META"  : "meta",
                prompt_label);
        } else {
            mvprintw(cmd_line, 0, "%s  [%s]  ][:%-12s                       ",
                commands, mode_names[mode], prompt_label);
        }
        int64_t lr = last_recv_us.load(std::memory_order_relaxed);
        int64_t silence_us = lr ? (now_us - lr) : 0;
        if (lr && silence_us > silence_alarm_us) {
            attron(COLOR_PAIR(3));
            mvprintw(stat_line, 0, "*** NO ORBS HEARD for %.1fs  try: sudo ip link set eth0 down && sudo ip link set eth0 up *** ", silence_us / 1'000'000.0);
            attroff(COLOR_PAIR(3));
        } else {
            // Worst per-orb miss: surfaces a single starving orb that the aggregate hides.
            float worst_miss = 0; uint32_t worst_serial = 0;
            for (auto [k, v] : slot_map)
                if (orb_miss_rate[v] > worst_miss) { worst_miss = orb_miss_rate[v]; worst_serial = k; }
            mvprintw(stat_line, 0, "Processing us:%5ld  Missed:%2d Miss ratio:%5.3f  Rate:%4.1fHz Act:%4.2f  worst %06x:%4.2f%s    ", elapsed_us, total_missed_pkts, missed_packet_ratio, live_hz, fleet_activity, worst_serial, worst_miss, g_autorate ? " [auto]" : (g_adaptive ? " [adaptive]" : ""));
        }
        mvprintw(headings, 0, "%s", heading);
        for(int y = 0; y < max_orbs; y++)
        {
            std::string s = rdata[y].str();
            uint32_t col = rgb565_to_888(s_packet.led_colour[y]);
            int cl = (mode == MODE_CLUSTER) ? cluster_state.smoothed_assignment[y] : -1;
            std::string rps = (mode == MODE_PROXIMITY) ? rdata[y].rssi_prox_str() : "";
            if (y == cursor_line) attron(COLOR_PAIR(2));
            mvprintw(statstart + y, 0, "%s %s %4d %06x %-30s",
                y == cursor_line ? ">>" : "  ",
                s.c_str(),
                cl, col, rps.c_str()
            );
            if (y == cursor_line) attroff(COLOR_PAIR(2));
        }
        // Location of return packet within window. The whole window is 20ms, the client sends
        // its response 8ms after it receives its multicast->unicast packet. So the round-trip latency 
        // can never be less than 8ms. In ideal operation, return packets should be evenly spread from 10 to ~18ms
        // We assume the screen is at least 128 characters wide, start at 8ms, extend to right at 20ms.
        // So call each character 100us
        //
        // Clear
        mvprintw(showlat, 0, "%128s", " ");
        for(int y = 0; y < max_orbs; y++)
        {
            int cpos = (odata[y].rlatency - 8000) / 100;
            if (cpos >= 0)
                mvprintw(showlat, cpos, "#####");
        }

        refresh();
        wrefresh(win);

    }

    void generate_led_colours_simon()
    {
        // Loop through the orbs with mappings
        json j;
        for(auto [k,v] : slot_map)
        {
            // k is the serial number of the Orb
            // v is the slot index in the data tables
            Eigen::Vector3f av = {rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z};
            av.normalize();
            Eigen::Vector3f up = Eigen::Vector3f::UnitZ();
            // Interp is between 0 and 1
            float interp = (1.0 + up.dot(av)) / 2.0;

            // Just do really simple interpolation between red and blue
            float r = interp;
            float b = 1.0 - interp;
            float g = 0;
            s_packet.led_colour[v] = rgb_to_565(r, g, b);

            // Pack stuff for transfer
            j[fmt::format("{:06x}", k)] = {
                rdata[v].accel_x, 
                rdata[v].accel_y, 
                rdata[v].accel_z,
            };
        }
        // Turn the json into a string
        std::string data = j.dump(4) + "\n";

        // This construction ensures the "orb_data" file always contains a single complete update
        // or nothing. This is because renaming a file is guaranteed an atomic operation in linux
        int fd = open("/tmp/orb_data_staging", O_CREAT | O_WRONLY | O_TRUNC, 0644);
        write(fd, data.c_str(), data.size());
        fsync(fd);
        close(fd);
        int dirfd = open("/tmp", O_DIRECTORY | O_RDONLY);
        fsync(dirfd);
        close(dirfd);
        rename("/tmp/orb_data_staging", "/tmp/orb_data");

    }


    // 12 vertices of an icosahedron with Z-axis alignment
    const Eigen::Vector3f icosa_vertices[12] = {
        // Top vertex (0)
        {0.0f, 0.0f, 1.0f},
        
        // Upper ring (1-5) - 5 vertices around upper part
        {0.724f, 0.526f, 0.447f},         // 1
        {-0.276f, 0.851f, 0.447f},        // 2
        {-0.894f, 0.0f, 0.447f},          // 3
        {-0.276f, -0.851f, 0.447f},       // 4
        {0.724f, -0.526f, 0.447f},        // 5
        
        // Lower ring (6-10) - 5 vertices around lower part  
        {0.894f, 0.0f, -0.447f},          // 6
        {0.276f, 0.851f, -0.447f},        // 7  
        {-0.724f, 0.526f, -0.447f},       // 8
        {-0.724f, -0.526f, -0.447f},      // 9
        {0.276f, -0.851f, -0.447f},       // 10
        
        // Bottom vertex (11)
        {0.0f, 0.0f, -1.0f}
    };

        // 12 colors equally spaced in hue (HSV with S=1, V=1, converted to RGB)
        const float ic[12] = {
            300.0/300,
            300.0/400,
            300.0/490,
            300.0/390,
            300.0/320,
            300.0/390,
            300.0/470,
            300.0/390,
            300.0/310,
            300.0/390,
            300.0/470,
            300.0/390
        };
        const Eigen::Vector3f icosa_colors[12] = {
            {1.0f*ic[0],    0.0f,       0.0f},          // Red (0°)            300
            {1.0f*ic[1],    0.5f*ic[1], 0.0f},          // Orange (30°)        400 
            {1.0f*ic[2],    1.0f*ic[2], 0.0f},          // Yellow (60°)        490
            {0.5f*ic[3],    1.0f*ic[3], 0.0f},          // Yellow-Green (90°)  390
            {0.0f,          1.0f*ic[4], 0.0f},          // Green (120°)        320
            {0.0f,          1.0f*ic[5], 0.5f*ic[5]},    // Spring Green (150°) 390
            {0.0f,          1.0f*ic[6], 1.0f*ic[6]},    // Cyan (180°)         470
            {0.0f,          0.5f*ic[7], 1.0f*ic[7]},    // Sky Blue (210°)     390
            {0.0f,          0.0f,       1.0f*ic[8]},    // Blue (240°)         310
            {0.5f*ic[9],    0.0f,       1.0f*ic[9]},    // Purple (270°)       390
            {1.0f*ic[10],   0.0f,       1.0f*ic[10]},   // Magenta (300°)      470
            {1.0f*ic[11],   0.0f,       0.5f*ic[11]}    // Rose (330°)         390
        };

        // Function to calculate brightness based on alignment with icosahedron vertex
        struct VertexAlignment {
            int vertex_id;
            float alignment;  // 0.0 to 1.0, where 1.0 is perfect alignment
            float brightness; // 0.0 to 1.0, the calculated brightness
        };

        VertexAlignment calculate_vertex_brightness(const Eigen::Vector3f& gravity_vector) {
            int best_vertex = 0;
            float max_dot_product = gravity_vector.dot(icosa_vertices[0]);
            
            // Find the vertex with maximum alignment
            for (int i = 1; i < 12; i++) {
                float dot_product = gravity_vector.dot(icosa_vertices[i]);
                if (dot_product > max_dot_product) {
                    max_dot_product = dot_product;
                    best_vertex = i;
                }
            }
            
            // Calculate alignment factor (dot product ranges from -1 to 1)
            // We want alignment to be 0 when orthogonal (dot = 0) and 1 when aligned (dot = 1)
            float alignment = std::max(0.0f, max_dot_product);
            
            // Apply brightness curve - you can adjust this for different feels:
            
            // Option 1: Linear brightness
            //float brightness = alignment;
            
            // Option 2: Quadratic (more dramatic falloff)
            //float brightness = alignment * alignment;
            
            // Option 3: Power curve (adjustable steepness)
            float power = 1.0f; // Higher values = steeper falloff
            float brightness = std::pow(alignment, power);
            
            // Option 4: Threshold with smooth transition
            // float threshold = 0.7f; // Only start lighting up when fairly aligned
            // float brightness = (alignment > threshold) ? (alignment - threshold) / (1.0f - threshold) : 0.0f;
            
            return {best_vertex, alignment, brightness};
        }

        // Helper function to apply brightness to RGB color (in 0-1 range)
        Eigen::Vector3f apply_brightness(const Eigen::Vector3f& color, float brightness) {
            return color * brightness;
        }

        // Phase 1.1
        void update_chaos_detection() {
            // Reset active notes tracking
            std::fill(system_state.notes_active, system_state.notes_active + 12, false);
            system_state.active_note_count = 0;
            
            // Check which notes are currently active
            for(auto [k,v] : slot_map) {
                Eigen::Vector3f av = {rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z};
                av.normalize();
                
                VertexAlignment alignment = calculate_vertex_brightness(av);
                if(alignment.brightness > 0.1f) { // Only count if reasonably bright
                    system_state.notes_active[alignment.vertex_id] = true;
                }
            }
            
            // Count active notes
            for(int i = 0; i < 12; i++) {
                if(system_state.notes_active[i]) system_state.active_note_count++;
            }
            
            // Check if chaos achieved
            if(!system_state.chaos_achieved) {
                system_state.chaos_achieved = (system_state.active_note_count >= 30);
            }
        }

        void update_root_selection() {
            if (!system_state.chaos_achieved || system_state.root_selected) {
                return;
            }
            
            // Reset vote counts
            std::fill(system_state.note_votes, system_state.note_votes + 12, 0);
            
            // Count votes for each note and track which orbs are voting for what
            std::map<int, std::vector<uint32_t>> note_voters; // note -> list of orb serials
            
            for(auto [k,v] : slot_map) {
                Eigen::Vector3f av = {rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z};
                av.normalize();
                
                VertexAlignment alignment = calculate_vertex_brightness(av);
                if(alignment.brightness > 0.1f) {
                    system_state.note_votes[alignment.vertex_id]++;
                    note_voters[alignment.vertex_id].push_back(k);
                }
            }
            
            // Find the note with most votes
            int max_votes = 0;
            int best_note = -1;
            for(int i = 0; i < 12; i++) {
                if(system_state.note_votes[i] > max_votes) {
                    max_votes = system_state.note_votes[i];
                    best_note = i;
                }
            }
            
            int min_votes_needed = 3;
            if(max_votes >= min_votes_needed) {
                system_state.root_selected = true;
                system_state.root_note = best_note;
                system_state.fifth_note = (system_state.root_note + 7) % 12;
                
                // Randomly choose major or minor
                bool is_major = (rand() % 2) == 0;
                system_state.third_note = (system_state.root_note + (is_major ? 4 : 3)) % 12; // Major = 4 semitones, Minor = 3
                system_state.seventh_note = (system_state.root_note + (is_major ? 11 : 10)) % 12; // Major7 = 11, Minor7 = 10
                system_state.eleventh_note = (system_state.root_note + 5) % 12; // 11th = 5 semitones (same for major/minor)
                
                // Lock root voters
                for(uint32_t orb_serial : note_voters[best_note]) {
                    system_state.locked_orbs.insert(orb_serial);
                    system_state.orb_locked_notes[orb_serial] = best_note;
                }
                
                // Get list of unlocked orbs
                std::vector<uint32_t> unlocked_orbs;
                for(auto [k,v] : slot_map) {
                    if(system_state.locked_orbs.count(k) == 0) {
                        unlocked_orbs.push_back(k);
                    }
                }
                
                // Assign remaining orbs to chord tones (fifth, third, seventh, eleventh)
                std::vector<int> chord_notes = {
                    system_state.fifth_note,
                    system_state.third_note, 
                    system_state.seventh_note,
                    system_state.eleventh_note
                };
                
                // Distribute unlocked orbs evenly across remaining chord tones
                for(size_t i = 0; i < unlocked_orbs.size(); i++) {
                    uint32_t orb_serial = unlocked_orbs[i];
                    int assigned_note = chord_notes[i % chord_notes.size()]; // Round-robin assignment
                    
                    system_state.locked_orbs.insert(orb_serial);
                    system_state.orb_locked_notes[orb_serial] = assigned_note;
                }
                
                // Mark all chord building as complete
                system_state.fifth_selected = true;
                system_state.chord_complete = true;
                system_state.chord_is_major = is_major;
            } else {
                system_state.root_note = best_note;
            }
        }

        void update_fifth_selection() {
            if (!system_state.root_selected || system_state.fifth_selected) {
                return; // Only run after root selected and before fifth selected
            }
            
            // Check if anyone is on the fifth note
            for(auto [k,v] : slot_map) {
                Eigen::Vector3f av = {rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z};
                av.normalize();
                
                VertexAlignment alignment = calculate_vertex_brightness(av);
                
                if(alignment.brightness > 0.1f && alignment.vertex_id == system_state.fifth_note) {
                    system_state.fifth_selected = true;
                    break; // Someone found the fifth!
                }
            }
        }

    //basic bpm from accel activity NOT fft 
    float calculate_bpm_from_accel(int slot_index, float accel_x, float accel_y, float accel_z) {
        static float accel_history[32][200] = {0}; // Increased to 20 readings for more stability
        static int history_index[32] = {0};
        
        // Calculate magnitude of acceleration
        float magnitude = sqrt(accel_x * accel_x + accel_y * accel_y + accel_z * accel_z);
        
        // Store in history
        accel_history[slot_index][history_index[slot_index]] = magnitude;
        history_index[slot_index] = (history_index[slot_index] + 1) % 20;
        
        // Calculate movement activity (variance in acceleration)
        float mean = 0;
        for(int i = 0; i < 200; i++) {
            mean += accel_history[slot_index][i];
        }
        mean /= 20.0f;
        
        float variance = 0;
        for(int i = 0; i < 200; i++) {
            float diff = accel_history[slot_index][i] - mean;
            variance += diff * diff;
        }
        variance /= 20.0f;
        
        // Convert variance to BPM with much more gradual scaling
        float activity = sqrt(variance);
        
        // Much less sensitive thresholds
        float min_activity = 0.001f;  // Very still
        float max_activity = 0.2f;    // Moderate activity (much lower max)
        
        activity = std::max(min_activity, std::min(max_activity, activity));
        float normalized_activity = (activity - min_activity) / (max_activity - min_activity);
        
        // Apply square root for even more gradual scaling
        normalized_activity = sqrt(normalized_activity);
        
        float target_bpm = 40.0f + normalized_activity * 80.0f; // 40-120 BPM range
        
        // Smooth transitions
        float smoothing = 0.08f;
        system_state.orb_bpm[slot_index] = system_state.orb_bpm[slot_index] * (1.0f - smoothing) + target_bpm * smoothing;
        
        return system_state.orb_bpm[slot_index];
    }

    // Simple FFT implementation for power-of-2 sizes
    void fft(std::vector<std::complex<float>>& x) {
        int N = x.size();
        if (N <= 1) return;
        
        // Divide
        std::vector<std::complex<float>> even(N/2), odd(N/2);
        for (int i = 0; i < N/2; i++) {
            even[i] = x[2*i];
            odd[i] = x[2*i + 1];
        }
        
        // Conquer
        fft(even);
        fft(odd);
        
        // Combine
        for (int i = 0; i < N/2; i++) {
            std::complex<float> t = std::polar(1.0f, -2.0f * (float)M_PI * (float)i / (float)N) * odd[i];            
            x[i + N/2] = even[i] - t;
        }
    }

    float calculate_bpm_from_accel_fft(int slot_index, float accel_x, float accel_y, float accel_z) {
        static float accel_history[32][64] = {0}; // 64 samples for FFT (power of 2)
        static int history_index[32] = {0};
        static int sample_count[32] = {0};
        
        // Calculate magnitude of acceleration
        float magnitude = sqrt(accel_x * accel_x + accel_y * accel_y + accel_z * accel_z);
        
        // Store in circular buffer
        accel_history[slot_index][history_index[slot_index]] = magnitude;
        history_index[slot_index] = (history_index[slot_index] + 1) % 64;
        sample_count[slot_index] = std::min(sample_count[slot_index] + 1, 64);
        
        // Need at least 64 samples for FFT
        if (sample_count[slot_index] < 64) {
            return system_state.orb_bpm[slot_index]; // Return previous value
        }
        
        // Prepare FFT input (reorder circular buffer)
        std::vector<std::complex<float>> fft_input(64);
        for (int i = 0; i < 64; i++) {
            int idx = (history_index[slot_index] + i) % 64;
            fft_input[i] = std::complex<float>(accel_history[slot_index][idx], 0);
        }
        
        // Remove DC component (average)
        float mean = 0;
        for (int i = 0; i < 64; i++) {
            mean += fft_input[i].real();
        }
        mean /= 64.0f;
        for (int i = 0; i < 64; i++) {
            fft_input[i] = std::complex<float>(fft_input[i].real() - mean, 0);
        }
        
        // Perform FFT
        fft(fft_input);
        
        // Calculate power spectrum and find peak in 0.1-3 Hz range
        float sample_rate = 50.0f; // 50 Hz (20ms period)
        float freq_resolution = sample_rate / 64.0f; // ~0.78 Hz per bin
        
        int min_bin = (int)(0.1f / freq_resolution); // 0.1 Hz
        int max_bin = (int)(3.0f / freq_resolution);  // 3.0 Hz
        min_bin = std::max(1, min_bin); // Skip DC (bin 0)
        max_bin = std::min(32, max_bin); // Only use first half (Nyquist)
        
        float max_power = 0;
        int peak_bin = min_bin;
        
        for (int i = min_bin; i <= max_bin; i++) {
            float power = std::norm(fft_input[i]); // |X[k]|^2
            if (power > max_power) {
                max_power = power;
                peak_bin = i;
            }
        }
        
        // Convert peak bin to frequency and then to BPM
        float peak_frequency = peak_bin * freq_resolution; // Hz
        float target_bpm = peak_frequency * 60.0f; // Convert Hz to BPM
        
        // Only update if we have significant power (movement detected)
        float power_threshold = 0.01f; // Adjust based on testing
        if (max_power < power_threshold) {
            target_bpm = 40.0f; // Default slow rate for no movement
        }
        
        // Constrain to reasonable range
        target_bpm = std::max(20.0f, std::min(120.0f, target_bpm));
        
        // Smooth the transitions
        float smoothing = 0.1f;
        system_state.orb_bpm[slot_index] = system_state.orb_bpm[slot_index] * (1.0f - smoothing) + target_bpm * smoothing;
        
        return system_state.orb_bpm[slot_index];
    }

    float calculate_bpm_from_peaks(int slot_index, float accel_x, float accel_y, float accel_z) {
        static float accel_history[32][1000] = {0}; // 20 seconds of data at 50Hz
        static int history_index[32] = {0};
        static float peak_times[32][20] = {0}; // Store timestamps of last 20 peaks
        static int peak_count[32] = {0};
        static int peak_write_index[32] = {0};
        static auto start_time = std::chrono::steady_clock::now();
        
        // Get current time
        auto current_time = std::chrono::steady_clock::now();
        float elapsed_seconds = std::chrono::duration<float>(current_time - start_time).count();
        
        // Calculate magnitude of acceleration
        float magnitude = sqrt(accel_x * accel_x + accel_y * accel_y + accel_z * accel_z);
        
        // Store in circular buffer
        accel_history[slot_index][history_index[slot_index]] = magnitude;
        int current_idx = history_index[slot_index];
        history_index[slot_index] = (history_index[slot_index] + 1) % 1000;
        
        // Need at least 10 samples for peak detection
        if (peak_count[slot_index] < 10) {
            peak_count[slot_index]++;
            return system_state.orb_bpm[slot_index];
        }
        
        // Peak detection: look for local maxima
        // Check if the point 3 samples back was a peak (to avoid detecting the same peak multiple times)
        int check_idx = (current_idx + 997) % 1000; // 3 samples back
        int before_idx = (current_idx + 996) % 1000; // 4 samples back
        int before2_idx = (current_idx + 995) % 1000; // 5 samples back
        int after_idx = (current_idx + 998) % 1000; // 2 samples back
        int after2_idx = (current_idx + 999) % 1000; // 1 sample back
        
        float check_val = accel_history[slot_index][check_idx];
        float before_val = accel_history[slot_index][before_idx];
        float before2_val = accel_history[slot_index][before2_idx];
        float after_val = accel_history[slot_index][after_idx];
        float after2_val = accel_history[slot_index][after2_idx];
        
        // Peak criteria: higher than neighbors AND above minimum threshold
        float min_peak_height = 1.2f; // Adjust based on your accelerometer readings
        bool is_peak = (check_val > before_val) && 
                    (check_val > before2_val) && 
                    (check_val > after_val) && 
                    (check_val > after2_val) && 
                    (check_val > min_peak_height);
        
        if (is_peak) {
            // Store peak timestamp
            peak_times[slot_index][peak_write_index[slot_index]] = elapsed_seconds - 0.06f; // 3 samples back in time
            peak_write_index[slot_index] = (peak_write_index[slot_index] + 1) % 20;
            
            // Calculate BPM from recent peaks
            std::vector<float> intervals;
            for (int i = 1; i < 20; i++) {
                int curr_peak = (peak_write_index[slot_index] + 20 - 1) % 20; // Most recent
                int prev_peak = (peak_write_index[slot_index] + 20 - 1 - i) % 20;
                
                if (peak_times[slot_index][prev_peak] > 0) {
                    float interval = peak_times[slot_index][curr_peak] - peak_times[slot_index][prev_peak];
                    if (interval > 0.01f && interval < 3.0f) { // 20-200 BPM range
                        intervals.push_back(interval);
                    }
                }
            }
            
            if (intervals.size() >= 3) {
                // Use median interval to avoid outliers
                std::sort(intervals.begin(), intervals.end());
                float median_interval = intervals[intervals.size() / 2];
                float target_bpm = 60.0f / median_interval;
                
                // Smooth the BPM changes
                float smoothing = 0.2f;
                system_state.orb_bpm[slot_index] = system_state.orb_bpm[slot_index] * (1.0f - smoothing) + target_bpm * smoothing;
            }
        }
        
        // If no recent peaks, gradually decay to baseline
        float time_since_last_peak = elapsed_seconds - peak_times[slot_index][(peak_write_index[slot_index] + 19) % 20];
        if (time_since_last_peak > 2.0f) { // No peaks for 2 seconds
            float decay_rate = 0.02f;
            system_state.orb_bpm[slot_index] = system_state.orb_bpm[slot_index] * (1.0f - decay_rate) + 0.0f * decay_rate;
        }
        
        return system_state.orb_bpm[slot_index];
    }

    void update_phase_sync(int slot_index, float accel_x, float accel_y, float accel_z) {
        static float activity_history[32][10] = {0}; // Rolling average
        static int activity_index[32] = {0};
        
        // Calculate movement activity
        float magnitude = sqrt(accel_x * accel_x + accel_y * accel_y + accel_z * accel_z);
        
        // Store in rolling buffer
        activity_history[slot_index][activity_index[slot_index]] = magnitude;
        activity_index[slot_index] = (activity_index[slot_index] + 1) % 10;
        
        // Calculate activity level (variance)
        float mean = 0;
        for(int i = 0; i < 10; i++) {
            mean += activity_history[slot_index][i];
        }
        mean /= 10.0f;
        
        float variance = 0;
        for(int i = 0; i < 10; i++) {
            float diff = activity_history[slot_index][i] - mean;
            variance += diff * diff;
        }
        variance /= 10.0f;
        
        float activity = sqrt(variance);
        
        // Convert activity to phase advance rate
        float phase_advance = activity * 0.1f; // Adjust sensitivity here
        
        // Advance this orb's phase based on movement
        system_state.pulse_phase[slot_index] += phase_advance;
        if(system_state.pulse_phase[slot_index] > 1.0f) {
            system_state.pulse_phase[slot_index] -= 1.0f; // Keep in 0-1 range
        }
    }

struct ZoneInfo {
    int best_vertex;
    float alignment;           // Raw dot product (-1 to 1)
    float alignment_percent;   // 0-100% alignment
    float angle_degrees;       // Angle from vertex in degrees
    std::string zone_type;     // "VERTEX", "TRANSITION", or "DEAD"
    Eigen::Vector3f color;
    float brightness;
};

// Helper function to calculate alignment percentage and angle
ZoneInfo calculate_zone_info(const Eigen::Vector3f& gravity_vector) {
    ZoneInfo info;
    
    // Find best vertex alignment
    info.best_vertex = 0;
    float max_dot_product = gravity_vector.dot(icosa_vertices[0]);
    
    for (int i = 1; i < 12; i++) {
        float dot_product = gravity_vector.dot(icosa_vertices[i]);
        if (dot_product > max_dot_product) {
            max_dot_product = dot_product;
            info.best_vertex = i;
        }
    }
    
    info.alignment = max_dot_product;
    
    // Calculate angle in degrees (acos of dot product)
    info.angle_degrees = acos(std::max(-1.0f, std::min(1.0f, max_dot_product))) * 180.0f / M_PI;
    
    // Calculate alignment percentage (0-100%)
    // Maps dot product to percentage where 1.0 = 100%, 0.0 = 0%
    info.alignment_percent = std::max(0.0f, max_dot_product * 100.0f);
    
    return info;
}

void print_threshold_test() {
    wprintw(win, "\n=== Threshold Testing Guide ===\n");
    wprintw(win, "Dot Product -> Angle -> Zone (current thresholds)\n");
    
    float test_dots[] = {1.0f, 0.95f, 0.9f, 0.85f, 0.8f, 0.75f, 0.7f, 0.65f, 0.6f, 0.5f, 0.3f, 0.0f};
    
    for (float dot : test_dots) {
        float angle = acos(dot) * 180.0f / M_PI;
        float percent = dot * 100.0f;
        
        const char* zone = "DEAD";
        if (dot >= 0.85f) zone = "VERTEX";
        else if (dot >= 0.65f) zone = "TRANSITION";
        
        wprintw(win, "  %.2f -> %5.1f° -> %6.1f%% -> %s\n", 
                dot, angle, percent, zone);
    }
    wprintw(win, "================================\n");
}

// Enhanced generate_led_colours with detailed debug output
void generate_led_colours() {
    // TUNABLE THRESHOLDS - Adjust these based on testing
    // You can also use angle-based thresholds if preferred
    const float VERTEX_THRESHOLD_DOT = 0.985f;      // ~32° cone around vertex
    const float DEADZONE_THRESHOLD_DOT = 0.89;    // ~49° cone
    
    // Alternative: Use angle-based thresholds (uncomment to use)
    // const float VERTEX_THRESHOLD_ANGLE = 32.0f;    // Degrees from vertex
    // const float DEADZONE_THRESHOLD_ANGLE = 49.0f;  // Degrees from vertex
    
    const float GREY_BRIGHTNESS = 0.05f;
    const float MAX_BRIGHTNESS = 1.0f;
    const Eigen::Vector3f GREY_COLOR(0.8f, 0.8f, 0.8f);
    
    static auto start_time = std::chrono::steady_clock::now();
    auto current_time = std::chrono::steady_clock::now();
    float elapsed_seconds = std::chrono::duration<float>(current_time - start_time).count();
    
    // Debug output control
    static auto last_debug_time = std::chrono::steady_clock::now();
    //bool should_debug = std::chrono::duration<float>(current_time - last_debug_time).count() > 0.5f; // Debug every 500ms
    bool should_debug = false;


    if (should_debug) {
        last_debug_time = current_time;
        wprintw(win, "\n=== Zone Debug (%.1fs) ===\n", elapsed_seconds);
        wprintw(win, "Thresholds: VERTEX=%.2f (%.1f°) DEAD=%.2f (%.1f°)\n", 
                VERTEX_THRESHOLD_DOT, acos(VERTEX_THRESHOLD_DOT) * 180.0f / M_PI,
                DEADZONE_THRESHOLD_DOT, acos(DEADZONE_THRESHOLD_DOT) * 180.0f / M_PI);
        wprintw(win, "%-8s %-4s %-6s %-6s %-5s %-12s %-6s\n", 
                "Serial", "Vert", "Align%", "Angle°", "Zone", "Color", "Bright");
        wprintw(win, "------------------------------------------------------------\n");
    }
    
    json j;
    std::map<std::string, int> zone_counts = {{"VERTEX", 0}, {"TRANSITION", 0}, {"DEAD", 0}};
    
    for(auto [k,v] : slot_map) {
        Eigen::Vector3f av = {rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z};
        av.normalize();
        
        // Calculate zone info
        ZoneInfo zone = calculate_zone_info(av);
        
        // Calculate BPM and phase sync
        system_state.orb_bpm[v] = calculate_bpm_from_peaks(v, rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z);
        update_phase_sync(v, rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z);
        
        // Determine zone and color
        Eigen::Vector3f final_color;
        float final_brightness;
        int active_vertex = -1;
        
        bool is_locked = system_state.locked_orbs.count(k) > 0;
        
        if (is_locked) {
            // Locked orb behavior
            int locked_note = system_state.orb_locked_notes[k];
            const Eigen::Vector3f& locked_color = icosa_colors[locked_note];
            
            if(system_state.chord_complete) {
                float fixed_bpm = 20.0f;
                float pulse_frequency = fixed_bpm / 60.0f;
                float base_phase = fmod(elapsed_seconds * pulse_frequency, 1.0f);
                float orb_phase = fmod(base_phase + system_state.pulse_phase[v], 1.0f);
                float pulse_brightness = 0.5f + 0.5f * sin(orb_phase * 2.0f * M_PI);
                final_brightness = pulse_brightness;
            } else {
                final_brightness = 1.0f;
            }
            final_color = locked_color;
            active_vertex = locked_note;
            zone.zone_type = "LOCKED";
        } else {
            // Determine zone based on alignment (using dot product thresholds)
            if (zone.alignment >= VERTEX_THRESHOLD_DOT) {
                // VERTEX ZONE - Full color at full brightness
                zone.zone_type = "VERTEX";
                final_color = icosa_colors[zone.best_vertex];
                final_brightness = MAX_BRIGHTNESS;  // Always full brightness for vertex
                active_vertex = zone.best_vertex;
                
            } else if (zone.alignment >= DEADZONE_THRESHOLD_DOT) {
                // TRANSITION ZONE - Full color at full brightness
                // (With your thresholds this zone is very small)
                float blend = ((zone.alignment - DEADZONE_THRESHOLD_DOT) / (VERTEX_THRESHOLD_DOT - DEADZONE_THRESHOLD_DOT));
                zone.zone_type = "TRANSITION";
                // Make the colour blend out slower, and the brightness quicker at first.
                // This stops the perceptual effect of the colour looking bleached out,
                // and the brightness rather flickery at the boundary to dead zone
                float b2 = 1 - pow(1 - blend, 2);
                final_color = blend_colour(icosa_colors[zone.best_vertex], GREY_COLOR, b2);
                //final_brightness = MAX_BRIGHTNESS;  // Full brightness for transition too
                float b3 = blend * blend;
                final_brightness = b3 * MAX_BRIGHTNESS + (1- b3) * GREY_BRIGHTNESS;
                active_vertex = zone.best_vertex;
                
            } else {
                // DEAD ZONE - Grey at fixed brightness
                zone.zone_type = "DEAD";
                final_color = GREY_COLOR;
                final_brightness = GREY_BRIGHTNESS;  // Fixed grey brightness
                active_vertex = -1;
            }
        }
        
        zone_counts[zone.zone_type]++;
        
        // Apply brightness to color
        Eigen::Vector3f output_color = final_color * final_brightness;
        output_color = output_color.cwiseMin(1.0f).cwiseMax(0.0f);
        
        // Convert to RGB565
        s_packet.led_colour[v] = rgb_to_565(output_color.x(), output_color.y(), output_color.z());
        
        // Debug output for each orb
        if (should_debug) {
            const char* color_name = active_vertex >= 0 ? 
                fmt::format("V{}", active_vertex).c_str() : "Grey";
            
            wprintw(win, "%06x   %-4d %-6.1f %-6.1f %-5s RGB(%3.0f,%3.0f,%3.0f) %-6.2f\n",
                    k,
                    zone.best_vertex,
                    zone.alignment_percent,
                    zone.angle_degrees,
                    zone.zone_type.substr(0, 5).c_str(),
                    output_color.x() * 255,
                    output_color.y() * 255,
                    output_color.z() * 255,
                    final_brightness);
        }
        
        // Store detailed data for JSON output. `cluster` is always present (0
        // when no clustering mode is active) so downstream consumers — e.g. the
        // SuperCollider bridge — always have a voice-routing field to read.
        j[fmt::format("{:06x}", k)] = {
            {"accel_x", rdata[v].accel_x},
            {"accel_y", rdata[v].accel_y},
            {"accel_z", rdata[v].accel_z},
            {"gyro_x", rdata[v].gyro_x},
            {"gyro_y", rdata[v].gyro_y},
            {"gyro_z", rdata[v].gyro_z},
            {"best_vertex", zone.best_vertex},
            {"vertex_id", active_vertex},
            {"alignment", zone.alignment},
            {"alignment_percent", zone.alignment_percent},
            {"angle_degrees", zone.angle_degrees},
            {"final_brightness", final_brightness},
            {"is_locked", is_locked},
            {"bpm", system_state.orb_bpm[v]},
            {"zone", zone.zone_type},
            {"cluster", 0},
            {"batt_v", rdata[v].batt_v},
            {"batt_i", rdata[v].batt_i},
            {"rssi", rdata[v].rssi},
            {"pkt_latency", (int)rdata[v].pkt_latency},
            {"missed", slot_watchdog[v] < slot_watchdog_limit - 1},
            {"miss_rate", orb_miss_rate[v]},
        };
    }
    
    if (should_debug) {
        wprintw(win, "------------------------------------------------------------\n");
        wprintw(win, "Zone Summary: VERTEX=%d TRANSITION=%d DEAD=%d\n", 
                zone_counts["VERTEX"], zone_counts["TRANSITION"], zone_counts["DEAD"]);
    }
    
    // Continue with chaos detection and root selection
    update_chaos_detection();
    update_root_selection();
    
    // System status
    std::string current_phase = "WAITING_FOR_CHAOS";
    if(system_state.chaos_achieved && !system_state.root_selected) {
        current_phase = "ROOT_SELECTION";
    } else if(system_state.chord_complete) {
        current_phase = "RHYTHM_PHASE";
    }
    
    j["system_status"] = {
        {"chaos_achieved", system_state.chaos_achieved},
        {"active_notes", system_state.active_note_count},
        {"root_selected", system_state.root_selected},
        {"chord_complete", system_state.chord_complete},
        {"root_note", system_state.root_note},
        {"phase", current_phase},
        {"zone_counts", {
            {"vertex", zone_counts["VERTEX"]},
            {"transition", zone_counts["TRANSITION"]},
            {"dead", zone_counts["DEAD"]}
        }}
    };

    // generate_led_colours is the default dispatch (ICOSAHEDRON mode and any
    // unrecognised mode). Hardcoded here since this is a free function without
    // access to the RealtimeThreads `mode` member.
    j["mode"] = "ICOSAHEDRON";
    j["network_stats"] = {
        {"miss_ratio", missed_packet_ratio},
        {"rate_hz", live_hz},
        {"activity", fleet_activity},
        {"total_missed_pkts", total_missed_pkts},
        {"num_orbs", (int)slot_map.size()},
        {"frame_num", loops},
        {"jitter_bins", bins},
    };

    // Write to file
    std::string data = j.dump(4) + "\n";
    int fd = open("/tmp/orb_data_staging", O_CREAT | O_WRONLY | O_TRUNC, 0644);
    write(fd, data.c_str(), data.size());
    fsync(fd);
    close(fd);
    int dirfd = open("/tmp", O_DIRECTORY | O_RDONLY);
    fsync(dirfd);
    close(dirfd);
    rename("/tmp/orb_data_staging", "/tmp/orb_data");
}


    /////////////////////////////////////////////// basic sound experience 
    // void generate_led_colours() {
    //     // TESTING SHORTCUT - uncomment to skip to rhythm phase
    //     // if(!system_state.chord_complete) {
    //     //     // Auto-assign all orbs to a test chord
    //     //     system_state.chaos_achieved = true;
    //     //     system_state.root_selected = true;
    //     //     system_state.chord_complete = true;
    //     //     system_state.root_note = 0; // C
    //     //     system_state.fifth_note = 7; // G
    //     //     system_state.third_note = 4; // E (major)
    //     //     system_state.seventh_note = 11; // B
    //     //     system_state.eleventh_note = 5; // F
            
    //     //     // IMPORTANT: Clear existing locks first
    //     //     system_state.locked_orbs.clear();
    //     //     system_state.orb_locked_notes.clear();
            
    //     //     // Lock all orbs to chord tones
    //     //     std::vector<int> chord_notes = {0, 7, 4, 11, 5}; // Root, fifth, third, seventh, eleventh
    //     //     int note_index = 0;
    //     //     for(auto [k,v] : slot_map) {
    //     //         system_state.locked_orbs.insert(k);  // k is the serial number
    //     //         system_state.orb_locked_notes[k] = chord_notes[note_index % chord_notes.size()];
    //     //         note_index++;
    //     //     }
    //     // }
    //     // Get current time for pulsing
    //     static auto start_time = std::chrono::steady_clock::now();
    //     auto current_time = std::chrono::steady_clock::now();
    //     float elapsed_seconds = std::chrono::duration<float>(current_time - start_time).count();
        
    //     json j;
    //     for(auto [k,v] : slot_map) {
    //         Eigen::Vector3f av = {rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z};
    //         av.normalize();

    //         // accel variance
    //         //system_state.orb_bpm[v] = calculate_bpm_from_accel(v, rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z);
    //         // accel fft
    //         //system_state.orb_bpm[v] = calculate_bpm_from_accel_fft(v, rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z);
    //         // accel peak detection for bpm
    //         system_state.orb_bpm[v] = calculate_bpm_from_peaks(v, rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z);
    //         // accel bpm phase
    //         update_phase_sync(v, rdata[v].accel_x, rdata[v].accel_y, rdata[v].accel_z);


    //         VertexAlignment alignment = calculate_vertex_brightness(av);
    //         const Eigen::Vector3f& base_color = icosa_colors[alignment.vertex_id];
            
    //         // Check if this orb is locked
    //         bool is_locked = system_state.locked_orbs.count(k) > 0;
    //         int display_note = alignment.vertex_id;
    //         float final_brightness;
    //         Eigen::Vector3f final_color;

    //         if(is_locked) {
    //         // Override with locked note
    //         display_note = system_state.orb_locked_notes[k];
    //         const Eigen::Vector3f& locked_color = icosa_colors[display_note];
            
    //         if(system_state.chord_complete) {
    //             float fixed_bpm = 20.0f; // Everyone pulses at same rate
    //             float pulse_frequency = fixed_bpm / 60.0f; // Convert to Hz
                
    //             // Each orb has its own phase that advances with movement
    //             float base_phase = fmod(elapsed_seconds * pulse_frequency, 1.0f);
    //             float orb_phase = fmod(base_phase + system_state.pulse_phase[v], 1.0f);
                
    //             float pulse_brightness = 0.5f + 0.5f * sin(orb_phase * 2.0f * M_PI);
    //             final_brightness = pulse_brightness;
    //         } else {
    //             final_brightness = 1.0f;
    //         }
            
    //             final_color = apply_brightness(locked_color, final_brightness);
    //         } else {
    //             // Normal behavior for unlocked orbs - should be very dim since we're in rhythm phase
    //             final_brightness = alignment.brightness * 0.1f;  // Make unlocked orbs very dim
    //             final_color = apply_brightness(base_color, final_brightness);
    //         }
            
    //         s_packet.led_colour[v] = rgb_to_565(final_color.x(), final_color.y(), final_color.z());
            
    //         j[fmt::format("{:06x}", k)] = {
    //             {"accel_x", rdata[v].accel_x},
    //             {"accel_y", rdata[v].accel_y}, 
    //             {"accel_z", rdata[v].accel_z},
    //             {"vertex_id", display_note},
    //             {"alignment", alignment.alignment},
    //             {"brightness", final_brightness},
    //             {"is_locked", is_locked},
    //             {"bpm", system_state.orb_bpm[v]}
    //         };
    //     }
        
    //     update_chaos_detection();
    //     update_root_selection();
        
    //     // Determine current phase
    //     std::string current_phase = "WAITING_FOR_CHAOS";
    //     if(system_state.chaos_achieved && !system_state.root_selected) {
    //         current_phase = "ROOT_SELECTION";
    //     } else if(system_state.chord_complete) {
    //         current_phase = "RHYTHM_PHASE";
    //     }
        
    //     j["system_status"] = {
    //         {"chaos_achieved", system_state.chaos_achieved},
    //         {"active_notes", system_state.active_note_count},
    //         {"root_selected", system_state.root_selected},
    //         {"chord_complete", system_state.chord_complete},
    //         {"root_note", system_state.root_note},
    //         {"phase", current_phase}
    //     };
            
    //     // Turn the json into a string
    //     std::string data = j.dump(4) + "\n";
        
    //     // This construction ensures the "orb_data" file always contains a single complete update
    //     // or nothing. This is because renaming a file is guaranteed an atomic operation in linux
    //     int fd = open("/tmp/orb_data_staging", O_CREAT | O_WRONLY | O_TRUNC, 0644);
    //     write(fd, data.c_str(), data.size());
    //     fsync(fd);
    //     close(fd);
    //     int dirfd = open("/tmp", O_DIRECTORY | O_RDONLY);
    //     fsync(dirfd);
    //     close(dirfd);
    //     rename("/tmp/orb_data_staging", "/tmp/orb_data");
    //     }








    // ------------------------------------------------------------------------
    // Make thread realtime and pin to a single CPU core for more deterministic
    // scheduling. Externally, the core should also be set without powersave and 
    // clock ramping.
    // Kernel command line:
    // isolcpus=2 nohz_full=2 rcu_nocbs=2 intel_pstate=disable intel_idle.max_cstate=1 processor.max_cstate=1 
    // /etc/rc.local to fix clock freq and prevent powersave
    // #!/bin/bash
    // cpufreq-set -c 2 -g performance
    // cpufreq-set -c 2 -d 2000000 -u 2000000
    // cpupower -c 2 idle-set -d 2
    // cpupower -c 2 idle-set -d 3
    // cpupower -c 2 idle-set -d 4
    // exit 0
    void configure_realtime(pthread_t thread_handle, int priority, int cpu)
    {
        // Set real-time priority
        sched_param sch_params = {};
        sch_params.sched_priority = priority;
        if (pthread_setschedparam(thread_handle, SCHED_FIFO, &sch_params) != 0)
        {
            std::cerr << "Failed to set thread scheduling: " << std::strerror(errno) << "\n";
        }

        // Set CPU affinity
        cpu_set_t cpuset;
        CPU_ZERO(&cpuset);
        CPU_SET(cpu, &cpuset);
        if (pthread_setaffinity_np(thread_handle, sizeof(cpu_set_t), &cpuset) != 0)
        {
            std::cerr << "Failed to set thread affinity: " << std::strerror(errno) << "\n";
        }
    }
};
int main(int argc, char **argv)
{
    for (int i = 1; i < argc; i++)
    {
        if (strcmp(argv[i], "-u") == 0 || strcmp(argv[i], "--unicast") == 0)
            unicast_mode = true;
        else if (strcmp(argv[i], "--adaptive") == 0)
            g_adaptive = true;
        else if (strcmp(argv[i], "--rate") == 0 && i + 1 < argc)
            g_rate_hz = atof(argv[++i]);
        else if (strcmp(argv[i], "--rmin") == 0 && i + 1 < argc)
            g_rmin_hz = atof(argv[++i]);
        else if (strcmp(argv[i], "--budget") == 0 && i + 1 < argc)
            g_budget_rt = atof(argv[++i]);
        else if (strcmp(argv[i], "--autorate") == 0)
            g_autorate = true;
        else if (strcmp(argv[i], "--miss") == 0 && i + 1 < argc)
            g_autorate_tgt = atof(argv[++i]);
        else if (strcmp(argv[i], "--armin") == 0 && i + 1 < argc)
            g_armin_hz = atof(argv[++i]);
        else if (strcmp(argv[i], "--actthr") == 0 && i + 1 < argc)
            g_act_thr = atof(argv[++i]);
        else
        {
            fprintf(stderr, "Usage: %s [-u|--unicast] [--rate HZ] [--adaptive] [--rmin HZ] [--budget RT]\n"
                            "          [--autorate [--miss RATIO] [--armin HZ] [--actthr G]]\n", argv[0]);
            return 1;
        }
    }
    if (g_rate_hz < 1)  g_rate_hz = 50.0;
    if (g_rmin_hz < 1)  g_rmin_hz = 25.0;
    if (g_budget_rt < 1) g_budget_rt = 600.0;
    if (g_autorate_tgt <= 0 || g_autorate_tgt >= 1) g_autorate_tgt = 0.10;
    if (g_armin_hz < 1) g_armin_hz = 20.0;
    if (g_armin_hz > g_rate_hz) g_armin_hz = g_rate_hz;  // floor can't exceed ceiling
    if (g_act_thr <= 0) g_act_thr = 0.05;

    constexpr int priority  = 80;                               // 1–99 for SCHED_FIFO
    constexpr int cpu       = 2;                                // Make sure this CPU is isolated if possible
    // loop period from --rate (default 50Hz => 20ms); --adaptive overrides per-frame
    auto period = std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::duration<double>(1.0 / g_rate_hz));
    std::vector<int> bins(100,0);

    // Refuse to start if we don't own the IP the orbs are trying to reach.
    // Runs before ncurses init so the warning stays readable in the terminal.
    if (!check_server_ip(server_ip)) {
        fprintf(stderr, "Server will start anyway, but orbs won't get your unicast replies "
                        "until the IP comes back. Proceeding...\n\n");
    }

    // Sigint handling
    struct sigaction sa;
    sa.sa_handler = handle_sigint;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    sigaction(SIGINT, &sa, NULL);

    // Ncurses setup, no line buffering, nonblocking, special keys
    int maxx, maxy;
    initscr();
    noecho();
    cbreak();
    curs_set(FALSE);
    keypad(stdscr, TRUE);
    nodelay(stdscr, TRUE);
    set_escdelay(1);
    win = newwin(winheight,100, winstart, 0);
    scrollok(win, TRUE);
    // Set up some colour pairs
    start_color();
    use_default_colors();
    init_pair(1, COLOR_BLACK, COLOR_WHITE);
    init_pair(2, COLOR_BLACK, COLOR_GREEN);
    init_pair(3, COLOR_WHITE, COLOR_RED);
    getmaxyx(stdscr, maxy, maxx);
    wprintw(win, "Window size width:%d height:%d\n", maxx, maxy);

    RealtimeThreads rt_threads(priority, cpu, period, bins);
    rt_threads.start();

    // Run until something says stop
    while(!stop && rt_threads.running)
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    rt_threads.stop();

    // Restore old terminal setup
    endwin();

    // Dump latency histogram
    for(int i = 0; i < bins.size(); i++)
    {
        printf("%3d %d\n", i, bins[i]);
    }

    return 0;
}
