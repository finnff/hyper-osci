#include "net_rx.h"

#include <Arduino.h>
#include <AsyncUDP.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <string.h>

#include "config.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "jitter_buffer.h"
#include "mic_in.h"
#include "mode_manager.h"
#include "protocol.h"
#include "renderer_local.h"
#include "timesync.h"

namespace {

JitterBuffer jb;
// Audio RX is AsyncUDP: a raw lwIP udp_recv callback feeding a 32-deep queue
// drained by the "async_udp" task. WiFiUDP's BSD-socket path sits behind a
// 6-slot per-socket mailbox (CONFIG_LWIP_UDP_RECVMBOX_SIZE, prebuilt libs) —
// post-stall AP bursts of ~60 packets overflowed it and lwIP dropped the
// excess below the socket layer, uncounted. Ctrl (2 Hz SYNC + rare CMD) and
// status (TX-only) fit the mailbox fine and stay on WiFiUDP in net_task.
AsyncUDP udp_audio;
WiFiUDP udp_ctrl;
WiFiUDP udp_status;

net_rx::CmdHandler cmd_handler = nullptr;

volatile bool g_radio_on = true;
volatile bool g_wifi_up = false;
volatile uint32_t g_last_audio_ms = 0;
volatile int8_t g_rssi = 0;

// Written by the async_udp task (audio) / audio task (underruns), read by
// net_task (send_status) and loopTask (stat) — volatile is enough: 32-bit
// aligned loads/stores are single instructions on RV32.
volatile uint32_t st_rx_packets = 0;
volatile uint32_t st_rx_dropped = 0;
volatile uint32_t st_underruns = 0;
volatile uint32_t st_seq_gaps = 0;    // gap EVENTS (any seq discontinuity)
volatile uint32_t st_lost_packets = 0;  // cumulative missing packets (gap sizes)
volatile uint32_t st_gap_resets = 0;  // deadline holes too long to conceal
// Lead (deadline - master now, ms) of the packet that last started an empty
// buffer: ~LEAD_US when it arrived promptly, less if it sat in transit.
volatile int32_t g_start_lead_ms = mode_manager::EV_NO_LEAD;
// Written by the async_udp task, and cleared by set_accept_audio() from the
// net/loop task while audio is not being accepted.
volatile uint32_t last_seq = 0;
volatile bool have_seq = false;
// LOCAL mode gate (set_accept_audio); checked before any accounting.
volatile bool g_accept_audio = true;

// Phase 2 (loop-fill concealment): fill method, bench drop pattern, the
// loop_frames markers, the fill counters and the per-fill-event ring.
// Same volatile note as above (async_udp task writes; loop/net tasks read).
volatile net_rx::FillMode g_fill_mode = net_rx::FillMode::kLoop;
volatile uint32_t g_drop_every = 0;  // 0 = off
volatile uint32_t g_drop_run = 0;
uint32_t g_drop_pkt = 0;  // position in the drop cycle (async_udp task only)
volatile uint16_t g_last_loop_frames = 0;  // last loop_frames field seen
volatile uint32_t g_last_zero_ms = 0;      // millis() when it was last 0
volatile bool g_have_zero_ms = false;
volatile bool g_fill_log = false;  // `log on` — one line per fill event

volatile uint32_t st_loop_fills = 0;
volatile uint32_t st_hold_fills = 0;
volatile uint32_t st_reanchor_gaps = 0;
volatile uint32_t st_gap_hist[5] = {0};

// Per-fill-event SPSC ring: producer = the async_udp task (handle_audio_
// packet), consumer = the UI loop (drain_fill_events). Never blocks; a full
// ring drops the newest event and counts it.
constexpr size_t FILL_LOG_SLOTS = 16;
net_rx::FillEvent fill_log[FILL_LOG_SLOTS];
volatile uint32_t fill_log_head = 0;  // next slot the producer writes
volatile uint32_t fill_log_tail = 0;  // next slot the consumer reads
volatile uint32_t fill_log_dropped_n = 0;  // events lost to a full ring

IPAddress controller_ip;
bool have_controller = false;

uint8_t pkt_buf[1500];

constexpr uint16_t JB_TARGET_FRAMES =
    (uint16_t)((uint32_t)JB_TARGET_DEPTH_MS * SAMPLE_RATE / 1000);

int32_t lead_ms(uint64_t deadline_us) {
  if (!timesync::valid()) return mode_manager::EV_NO_LEAD;
  return (int32_t)(((int64_t)(deadline_us - timesync::master_now_us())) / 1000);
}

// Runs in the "async_udp" task via on_audio_packet(); buf points into the
// lwIP pbuf and is only valid for the duration of the call — everything is
// copied out (header into locals, samples into the jitter buffer) before
// returning. Must never block: the JB/timesync critical sections are
// spinlocks, and blocking here would stall AsyncUDP's 32-deep queue.
void handle_audio_packet(const uint8_t* buf, size_t len) {
  if (len < sizeof(HypeHeader) + sizeof(HypeAudioPayload)) return;
  // Copy into aligned locals: the pbuf payload is only guaranteed 2-byte
  // aligned, and the header holds a uint64 at offset 12.
  HypeHeader h;
  HypeAudioPayload ap;
  memcpy(&h, buf, sizeof(h));
  memcpy(&ap, buf + sizeof(h), sizeof(ap));
  if (h.magic != HYPE_MAGIC || h.version != HYPE_PROTO_VERSION) return;
  if (h.type != HYPE_AUDIO) return;
  if (!g_accept_audio) return;  // LOCAL: not ours to buffer or count
  if (ap.sample_rate != SAMPLE_RATE) {
    st_rx_dropped++;
    return;
  }
  const size_t need = sizeof(HypeHeader) + sizeof(HypeAudioPayload) +
                      (size_t)ap.frame_count * 2 * sizeof(int16_t);
  if (len < need || ap.frame_count == 0 ||
      ap.frame_count > 4 * HYPE_AUDIO_FRAMES) {
    st_rx_dropped++;
    return;
  }

  // Bench tooling (RAM only): drop `run` consecutive AUDIO packets at the
  // head of every `every`-packet cycle, BEFORE the seq accounting so the
  // holes count as real loss.
  if (g_drop_every > 0) {
    const bool drop = (g_drop_pkt < g_drop_run);
    g_drop_pkt = (g_drop_pkt + 1) % g_drop_every;
    if (drop) return;
  }

  // SYNC_PULSE marks the seq/timestamp discontinuity as intentional — it must
  // not count in loss statistics (protocol.md §5.4).
  uint32_t seq_delta = 0;
  bool seq_contiguous = false;  // have_seq && delta == 1: a deadline hole with
                                // nothing missing (a controller re-anchor)
  uint16_t missing_pkts = 0;  // for the fill-event log (capped like st_lost)
  if (have_seq && !(h.flags & HYPE_FLAG_SYNC_PULSE)) {
    seq_delta = h.seq - last_seq;  // unsigned: wrap-safe
    if (seq_delta == 1) {
      seq_contiguous = true;
    } else {
      st_seq_gaps++;  // gap events (stats only; a delta==0 duplicate counts)
      if (seq_delta > 1) {
        // Cumulative loss inferred from gap sizes — this is the only place
        // lwIP-level (pre-socket) drops become visible. Cap per-event so an
        // unflagged controller restart / seq re-anchor doesn't poison the
        // counter.
        const uint32_t missing = seq_delta - 1;
        if (missing <= 1000) st_lost_packets += missing;
        missing_pkts = (uint16_t)(missing <= 1000 ? missing : 1000);
        // Hole-size histogram (packets missing): 1, 2, 3-4, 5-30, >30.
        if (missing == 1) st_gap_hist[0]++;
        else if (missing == 2) st_gap_hist[1]++;
        else if (missing <= 4) st_gap_hist[2]++;
        else if (missing <= 30) st_gap_hist[3]++;
        else st_gap_hist[4]++;
      }
    }
  }
  last_seq = h.seq;
  have_seq = true;

  // Stale on arrival? (Only judged when the clock is synced.)
  if (timesync::valid() &&
      h.timestamp_us + (uint64_t)DEADLINE_SLACK_US < timesync::master_now_us()) {
    st_rx_dropped++;
    return;
  }

  // Controller marks intentional discontinuities (seek, preset change).
  if (h.flags & HYPE_FLAG_SYNC_PULSE) jb.reset();

  // Continuity is judged by deadlines, not sequence numbers: a lost packet is
  // a hole we conceal; only a hole we can't conceal rebuffers (docs/
  // protocol.md §6). Loop-copy covers up to HYPE_LOOP_FILL_MAX_MS when the
  // field allows; without a loop, last-value hold covers only up to
  // HOLD_FILL_MAX_MS — a longer parked dot is worse than a reset (Phase 3).
  // ±1 ms tolerance absorbs timestamp rounding.
  const int16_t* samples =
      (const int16_t*)(buf + sizeof(HypeHeader) + sizeof(HypeAudioPayload));
  bool ok;
  if (jb.depth_frames() == 0) {
    g_start_lead_ms = lead_ms(h.timestamp_us);
    ok = jb.push(samples, ap.frame_count, h.timestamp_us);
  } else {
    const int64_t delta =
        (int64_t)(h.timestamp_us - jb.tail_deadline_us());
    const int64_t hold_max_us = (int64_t)HOLD_FILL_MAX_MS * 1000;
    bool fill_tried = false;
    uint16_t filled = 0;
    bool used_loop = false;
    if (delta > 1000 && delta <= (int64_t)HYPE_LOOP_FILL_MAX_MS * 1000) {
      // Fill so the buffer stays deadline-contiguous, then append the new
      // packet. The fill uses THIS packet's loop_frames — it describes the
      // content around the hole, and the controller zeroes it whenever the
      // source would be stale (pattern change, settle window).
      fill_tried = true;
      const uint16_t n = (uint16_t)((delta * SAMPLE_RATE) / 1000000);
      if (g_fill_mode == net_rx::FillMode::kLoop && ap.loop_frames != 0 &&
          ap.loop_frames <= HYPE_LOOP_MAX_FRAMES) {
        filled = jb.push_loop(n, ap.loop_frames);
        used_loop = (filled > 0);
      }
      // Forced hold / no usable loop: hold only a short hole.
      if (filled == 0 && delta <= hold_max_us) filled = jb.push_hold(n);
    }
    if (delta < -1000) {
      // Duplicate / reordered / overlapping packet: already have that time.
      st_rx_dropped++;
      return;
    } else if (fill_tried && (filled > 0 || delta <= hold_max_us)) {
      if (filled > 0) {
        if (used_loop) st_loop_fills++;
        else st_hold_fills++;
        // One line per fill (printed by the UI loop — the async_udp task must
        // never Serial.print: hot path, shared UART).
        if (g_fill_log) {
          const uint32_t hpos = fill_log_head;
          if (hpos - fill_log_tail < FILL_LOG_SLOTS) {
            net_rx::FillEvent& e = fill_log[hpos % FILL_LOG_SLOTS];
            e.method = (uint8_t)(used_loop ? net_rx::FillMode::kLoop
                                           : net_rx::FillMode::kHold);
            e.frames = filled;
            e.pkts = missing_pkts;
            e.since0_ms = (ap.loop_frames == 0) ? 0
                          : g_have_zero_ms     ? millis() - g_last_zero_ms
                                               : UINT32_MAX;
            fill_log_head = hpos + 1;
          } else {
            fill_log_dropped_n++;
          }
        }
      }
      if (seq_contiguous) st_reanchor_gaps++;  // a re-anchor, nothing lost
      ok = jb.push(samples, ap.frame_count, h.timestamp_us);
    } else if (delta > 1000) {
      // A hole we can't conceal: past the loop-fill limit (epoch jump, long
      // outage), or past the hold limit with no loop to copy. Rebuffer.
      jb.reset();
      st_gap_resets++;
      if (seq_contiguous) st_reanchor_gaps++;  // a re-anchor, nothing lost
      g_start_lead_ms = lead_ms(h.timestamp_us);
      mode_manager::note_reset((uint32_t)(delta / 1000), g_start_lead_ms);
      ok = jb.push(samples, ap.frame_count, h.timestamp_us);
    } else {
      ok = jb.push(samples, ap.frame_count, h.timestamp_us);  // contiguous
    }
  }
  if (!ok) {
    st_rx_dropped++;  // buffer full — controller running too far ahead
    return;
  }

  // Liveness/stats reflect *accepted* audio only, so a desynced or overflowing
  // stream doesn't hold stream_active() true while nothing can play. The
  // loop marker too: `stat` shows the field of audio that was buffered, and
  // its last 0 anchors the fill log (ms since the last pattern change seen).
  st_rx_packets++;
  g_last_audio_ms = millis();
  g_last_loop_frames = ap.loop_frames;
  if (ap.loop_frames == 0) {
    g_have_zero_ms = true;
    g_last_zero_ms = millis();
  }
  if (!jb.started() && jb.depth_frames() >= JB_TARGET_FRAMES) {
    jb.set_started(true);
  }
}

void on_audio_packet(AsyncUDPPacket& packet) {
  handle_audio_packet(packet.data(), packet.length());
}

void send_status();

void handle_ctrl_packet(size_t len, IPAddress src) {
  if (len < sizeof(HypeHeader)) return;
  const HypeHeader* h = (const HypeHeader*)pkt_buf;
  if (h->magic != HYPE_MAGIC || h->version != HYPE_PROTO_VERSION) return;
  controller_ip = src;
  have_controller = true;
  switch (h->type) {
    case HYPE_SYNC:
      timesync::on_beacon(h->timestamp_us);
      break;
    case HYPE_CMD:
      if (cmd_handler != nullptr && len > sizeof(HypeHeader)) {
        cmd_handler((const char*)(pkt_buf + sizeof(HypeHeader)),
                    len - sizeof(HypeHeader));
        // Confirm at once rather than at the next 1 Hz beacon: the
        // controller re-sends an unconfirmed set_mode until a STATUS agrees.
        send_status();
      }
      break;
    default:
      break;
  }
}

void send_status() {
  if (!have_controller || !g_wifi_up) return;
  static uint32_t status_seq = 0;

  uint8_t out[sizeof(HypeHeader) + sizeof(HypeStatusPayload)];
  HypeHeader* h = (HypeHeader*)out;
  h->magic = HYPE_MAGIC;
  h->version = HYPE_PROTO_VERSION;
  h->type = HYPE_STATUS;
  h->flags = 0;
  h->seq = status_seq++;
  // STATUS carries the slave's RAW local clock (protocol.md §3.4) — the
  // controller must never mix it with its own.
  h->timestamp_us = (uint64_t)esp_timer_get_time();

  HypeStatusPayload* s = (HypeStatusPayload*)(out + sizeof(HypeHeader));
  WiFi.macAddress(s->mac);
  s->slave_id = mode_manager::slave_id();
  s->mode = (uint8_t)mode_manager::mode();
  s->source = mode_manager::active_source();
  s->rssi_dbm = g_rssi;
  s->vbat_mv = mic_in::vbat_mv();
  s->buffer_depth_frames = jb.depth_frames();
  s->rx_packets = st_rx_packets;
  s->rx_dropped = st_rx_dropped;
  s->underruns = st_underruns;
  s->uptime_s = millis() / 1000;
  int64_t off = timesync::offset_us();
  if (off > INT32_MAX) off = INT32_MAX;
  if (off < INT32_MIN) off = INT32_MIN;
  s->clock_offset_us = (int32_t)off;
  s->local_pattern = (uint8_t)renderer_local::pattern();
  s->lost_packets = st_lost_packets;
  s->loop_fills = st_loop_fills;
  s->hold_fills = st_hold_fills;
  s->gap_resets = st_gap_resets;
  s->reanchor_gaps = st_reanchor_gaps;
  for (int i = 0; i < 5; i++) s->gap_hist[i] = st_gap_hist[i];

  udp_status.beginPacket(controller_ip, PORT_STATUS);
  udp_status.write(out, sizeof(out));
  udp_status.endPacket();
}

void wifi_connect() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // modem sleep adds 100+ ms jitter — required OFF
  WiFi.begin(WIFI_SSID_DEFAULT, WIFI_PASS_DEFAULT);
}

void on_wifi_up() {
  // WiFi.setSleep(false) before begin() does not reliably stick in core 3.x —
  // with power save active the AP buffers our unicast audio between DTIM
  // wakeups: measured as a ~110 ms radio stall every 1.4 s (dot-on-scope
  // dropouts). Force it here, where the STA is definitely started, and
  // surface the live value in `stat` (ps=) so a regression is visible.
  esp_wifi_set_ps(WIFI_PS_NONE);
  // listenMulticast = IGMP join + bind ANY:5000, mirroring the old
  // beginMulticast (audio is unicast on the wire; the join is belt-and-
  // braces). Called on every down->up transition, so bind and group
  // membership are re-established after a WiFi drop (re-listen on the same
  // pcb is safe; joins are ref-counted).
  udp_audio.listenMulticast(IPAddress(MCAST_GROUP), PORT_AUDIO);
  // The async_udp task defaults to priority 3 — below net_task (5). Bump it
  // so JB pushes don't queue behind WiFi housekeeping, but keep it below the
  // audio task (10) so a packet burst can never delay an I2S deadline.
  static bool prio_bumped = false;
  if (!prio_bumped) {
    TaskHandle_t t = xTaskGetHandle("async_udp");
    if (t != nullptr) {
      vTaskPrioritySet(t, 8);
      prio_bumped = true;
    }
  }
  udp_ctrl.beginMulticast(IPAddress(MCAST_GROUP), PORT_CTRL);
  udp_status.begin(0);  // TX only, ephemeral port
}

void on_wifi_down() {
  udp_audio.close();  // pcb stays bound; on_wifi_up() re-listens
  udp_ctrl.stop();
  udp_status.stop();
  jb.reset();
  // Cross-task write (net_task) vs the async_udp handler: benign — worst
  // case is one spurious gap count on the first packet after reconnect.
  have_seq = false;
}

void net_task(void*) {
  uint32_t last_status_ms = 0;
  uint32_t connect_started_ms = 0;
  bool connecting = false;

  for (;;) {
    if (!g_radio_on) {
      if (g_wifi_up || connecting) {
        // Say goodbye while the radio is still up: this STATUS carries
        // mode=LOCAL, so the controller's target filter drops us from the
        // audio fan-out on its next tick (~1 s) instead of waiting out its
        // 30 s roster eviction with 200 pkt/s aimed at a dead station.
        if (g_wifi_up) send_status();
        on_wifi_down();
        WiFi.disconnect(true, false);
        WiFi.mode(WIFI_OFF);
        g_wifi_up = false;
        connecting = false;
      }
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }

    const bool connected = (WiFi.status() == WL_CONNECTED);
    if (connected && !g_wifi_up) {
      g_wifi_up = true;
      connecting = false;
      on_wifi_up();
    } else if (!connected && g_wifi_up) {
      g_wifi_up = false;
      on_wifi_down();
    }

    if (!connected) {
      const uint32_t now = millis();
      if (!connecting) {
        wifi_connect();
        connecting = true;
        connect_started_ms = now;
      } else if (now - connect_started_ms > WIFI_CONNECT_TIMEOUT_MS) {
        WiFi.disconnect(true, false);
        connecting = false;  // retry next iteration
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    // Connected: audio arrives via the AsyncUDP callback; only the ctrl
    // socket is drained here.
    int len;
    while ((len = udp_ctrl.parsePacket()) > 0) {
      if (len > (int)sizeof(pkt_buf)) {
        udp_ctrl.clear();
        continue;
      }
      IPAddress src = udp_ctrl.remoteIP();
      int r = udp_ctrl.read(pkt_buf, len);
      if (r > 0) handle_ctrl_packet((size_t)r, src);
    }

    const uint32_t now = millis();
    if (now - last_status_ms >= STATUS_INTERVAL_MS) {
      last_status_ms = now;
      g_rssi = (int8_t)WiFi.RSSI();
      send_status();
    }

    // 5 ms is fine now that audio is callback-driven: this loop only
    // services 2 Hz SYNC beacons, rare CMDs, and the 1 Hz status TX.
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

}  // namespace

namespace net_rx {

void init(bool radio_on) {
  g_radio_on = radio_on;
  // Register before the task exists so there is no window where a bound
  // socket could deliver to a null handler.
  udp_audio.onPacket(on_audio_packet);
  xTaskCreate(net_task, "net_rx", 6144, nullptr, 5, nullptr);
}

void set_cmd_handler(CmdHandler h) { cmd_handler = h; }

void set_radio(bool on) { g_radio_on = on; }

bool radio_enabled() { return g_radio_on; }

bool wifi_connected() { return g_wifi_up; }

const char* ps_mode() {
  wifi_ps_type_t ps;
  if (esp_wifi_get_ps(&ps) != ESP_OK) return "?";
  switch (ps) {
    case WIFI_PS_NONE:
      return "none";
    case WIFI_PS_MIN_MODEM:
      return "min";
    default:
      return "max";
  }
}

bool stream_active() {
  return g_wifi_up && (millis() - g_last_audio_ms) < STREAM_TIMEOUT_MS &&
         st_rx_packets > 0;
}

int32_t start_lead_ms() { return g_start_lead_ms; }

Pull pull_block(int16_t* frames, size_t frame_count) {
  if (!jb.started()) return Pull::IDLE;

  // Deadline policy (docs/protocol.md §4), only enforced with a valid clock.
  if (timesync::valid() && jb.depth_frames() >= frame_count) {
    if (jb.head_deadline_us() >
        timesync::master_now_us() + (uint64_t)DEADLINE_SLACK_US) {
      return Pull::EARLY;  // caller renders local/concealment this block
    }
    // Late: catch up in whole blocks, re-reading the head deadline as we skip.
    while (jb.depth_frames() > frame_count &&
           jb.head_deadline_us() + (uint64_t)DEADLINE_SLACK_US <
               timesync::master_now_us()) {
      jb.skip((uint16_t)frame_count);
    }
  }

  if (!jb.pop(frames, (uint16_t)frame_count)) {
    st_underruns++;
    jb.set_started(false);  // rebuffer to target before resuming
    return Pull::UNDERRUN;
  }
  return Pull::PLAYED;
}

uint32_t flush_count() { return jb.flushes(); }

void set_accept_audio(bool on) {
  if (!on) {
    g_accept_audio = false;  // gate first, so nothing lands after the flush
    jb.reset();
    return;
  }
  if (g_accept_audio) return;  // NETWORK <-> HYBRID: keep the stream
  // A packet already past the gate when it closed may have landed after the
  // flush; and the seq jump across the LOCAL stint is not loss.
  jb.reset();
  have_seq = false;
  g_accept_audio = true;
}

Stats stats() {
  Stats s;
  s.rx_packets = st_rx_packets;
  s.rx_dropped = st_rx_dropped;
  s.underruns = st_underruns;
  s.seq_gaps = st_seq_gaps;
  s.lost_packets = st_lost_packets;
  s.gap_resets = st_gap_resets;
  s.loop_fills = st_loop_fills;
  s.hold_fills = st_hold_fills;
  s.reanchor_gaps = st_reanchor_gaps;
  for (int i = 0; i < 5; i++) s.gap_hist[i] = st_gap_hist[i];
  s.fill_mode = g_fill_mode;
  s.last_loop_frames = g_last_loop_frames;
  s.droptest_every = g_drop_every;
  s.droptest_run = g_drop_run;
  s.rssi_dbm = g_rssi;
  s.wifi_up = g_wifi_up;
  s.stream_up = stream_active();
  s.buffer_depth_frames = jb.depth_frames();
  return s;
}

void set_fill_mode(FillMode m) { g_fill_mode = m; }

void set_droptest(uint32_t every, uint32_t run) {
  g_drop_every = every;
  g_drop_run = (every > 0) ? run : 0;
}

void set_fill_log(bool on) { g_fill_log = on; }

size_t drain_fill_events(FillEvent* out, size_t cap) {
  size_t n = 0;
  while (n < cap) {
    const uint32_t t = fill_log_tail;
    if (t == fill_log_head) break;
    out[n++] = fill_log[t % FILL_LOG_SLOTS];
    fill_log_tail = t + 1;
  }
  return n;
}

uint32_t fill_log_dropped() { return fill_log_dropped_n; }

}  // namespace net_rx
