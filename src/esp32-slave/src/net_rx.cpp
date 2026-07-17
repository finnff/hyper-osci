#include "net_rx.h"

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <string.h>

#include "config.h"
#include "esp_timer.h"
#include "jitter_buffer.h"
#include "mic_in.h"
#include "mode_manager.h"
#include "protocol.h"
#include "timesync.h"

namespace {

JitterBuffer jb;
WiFiUDP udp_audio;
WiFiUDP udp_ctrl;
WiFiUDP udp_status;

net_rx::CmdHandler cmd_handler = nullptr;

volatile bool g_radio_on = true;
volatile bool g_wifi_up = false;
volatile uint32_t g_last_audio_ms = 0;
volatile int8_t g_rssi = 0;

uint32_t st_rx_packets = 0;
uint32_t st_rx_dropped = 0;
uint32_t st_underruns = 0;
uint32_t st_seq_gaps = 0;
uint32_t last_seq = 0;
bool have_seq = false;

IPAddress controller_ip;
bool have_controller = false;

uint8_t pkt_buf[1500];

constexpr uint16_t JB_TARGET_FRAMES =
    (uint16_t)((uint32_t)JB_TARGET_DEPTH_MS * SAMPLE_RATE / 1000);

void handle_audio_packet(size_t len) {
  if (len < sizeof(HypeHeader) + sizeof(HypeAudioPayload)) return;
  const HypeHeader* h = (const HypeHeader*)pkt_buf;
  if (h->magic != HYPE_MAGIC || h->version != HYPE_PROTO_VERSION) return;
  if (h->type != HYPE_AUDIO) return;
  const HypeAudioPayload* ap =
      (const HypeAudioPayload*)(pkt_buf + sizeof(HypeHeader));
  if (ap->sample_rate != SAMPLE_RATE) {
    st_rx_dropped++;
    return;
  }
  const size_t need = sizeof(HypeHeader) + sizeof(HypeAudioPayload) +
                      (size_t)ap->frame_count * 2 * sizeof(int16_t);
  if (len < need || ap->frame_count == 0 ||
      ap->frame_count > 4 * HYPE_AUDIO_FRAMES) {
    st_rx_dropped++;
    return;
  }

  if (have_seq && h->seq != last_seq + 1) st_seq_gaps++;  // stats only
  last_seq = h->seq;
  have_seq = true;

  // Stale on arrival? (Only judged when the clock is synced.)
  if (timesync::valid() &&
      h->timestamp_us + (uint64_t)DEADLINE_SLACK_US < timesync::master_now_us()) {
    st_rx_dropped++;
    return;
  }

  // Controller marks intentional discontinuities (seek, preset change).
  if (h->flags & HYPE_FLAG_SYNC_PULSE) jb.reset();

  // Continuity is judged by deadlines, not sequence numbers: a lost packet is
  // a small hole we conceal with last-value hold; only large discontinuities
  // rebuffer (docs/protocol.md §6). ±1 ms tolerance absorbs timestamp rounding.
  const int16_t* samples =
      (const int16_t*)(pkt_buf + sizeof(HypeHeader) + sizeof(HypeAudioPayload));
  bool ok;
  if (jb.depth_frames() == 0) {
    ok = jb.push(samples, ap->frame_count, h->timestamp_us);
  } else {
    const int64_t delta =
        (int64_t)(h->timestamp_us - jb.tail_deadline_us());
    if (delta > 1000 && delta <= 20000) {
      // Small gap (lost packet(s), ≤20 ms): fill with last-value hold so the
      // buffer stays deadline-contiguous, then append the new packet.
      jb.push_hold((uint16_t)((delta * SAMPLE_RATE) / 1000000));
      ok = jb.push(samples, ap->frame_count, h->timestamp_us);
    } else if (delta < -1000) {
      // Duplicate / reordered / overlapping packet: already have that time.
      st_rx_dropped++;
      return;
    } else if (delta > 20000) {
      // Large discontinuity (epoch jump, long outage): rebuffer from here.
      jb.reset();
      ok = jb.push(samples, ap->frame_count, h->timestamp_us);
    } else {
      ok = jb.push(samples, ap->frame_count, h->timestamp_us);  // contiguous
    }
  }
  if (!ok) {
    st_rx_dropped++;  // buffer full — controller running too far ahead
    return;
  }

  // Liveness/stats reflect *accepted* audio only, so a desynced or overflowing
  // stream doesn't hold stream_active() true while nothing can play.
  st_rx_packets++;
  g_last_audio_ms = millis();

  if (!jb.started() && jb.depth_frames() >= JB_TARGET_FRAMES) {
    jb.set_started(true);
  }
}

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
  udp_audio.beginMulticast(IPAddress(MCAST_GROUP), PORT_AUDIO);
  udp_ctrl.beginMulticast(IPAddress(MCAST_GROUP), PORT_CTRL);
  udp_status.begin(0);  // TX only, ephemeral port
}

void on_wifi_down() {
  udp_audio.stop();
  udp_ctrl.stop();
  udp_status.stop();
  jb.reset();
  have_seq = false;
}

void net_task(void*) {
  uint32_t last_status_ms = 0;
  uint32_t connect_started_ms = 0;
  bool connecting = false;

  for (;;) {
    if (!g_radio_on) {
      if (g_wifi_up || connecting) {
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

    // Connected: drain both sockets, then a short sleep.
    int len;
    while ((len = udp_audio.parsePacket()) > 0) {
      if (len > (int)sizeof(pkt_buf)) {
        udp_audio.clear();
        st_rx_dropped++;
        continue;
      }
      int r = udp_audio.read(pkt_buf, len);
      if (r > 0) handle_audio_packet((size_t)r);
    }
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

    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

}  // namespace

namespace net_rx {

void init(bool radio_on) {
  g_radio_on = radio_on;
  xTaskCreate(net_task, "net_rx", 6144, nullptr, 5, nullptr);
}

void set_cmd_handler(CmdHandler h) { cmd_handler = h; }

void set_radio(bool on) { g_radio_on = on; }

bool radio_enabled() { return g_radio_on; }

bool wifi_connected() { return g_wifi_up; }

bool stream_active() {
  return g_wifi_up && (millis() - g_last_audio_ms) < STREAM_TIMEOUT_MS &&
         st_rx_packets > 0;
}

bool pull_block(int16_t* frames, size_t frame_count) {
  if (!jb.started()) return false;

  // Deadline policy (docs/protocol.md §4), only enforced with a valid clock.
  if (timesync::valid() && jb.depth_frames() >= frame_count) {
    if (jb.head_deadline_us() >
        timesync::master_now_us() + (uint64_t)DEADLINE_SLACK_US) {
      return false;  // too early — caller renders local/silence this block
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
    return false;
  }
  return true;
}

Stats stats() {
  Stats s;
  s.rx_packets = st_rx_packets;
  s.rx_dropped = st_rx_dropped;
  s.underruns = st_underruns;
  s.seq_gaps = st_seq_gaps;
  s.rssi_dbm = g_rssi;
  s.wifi_up = g_wifi_up;
  s.stream_up = stream_active();
  s.buffer_depth_frames = jb.depth_frames();
  return s;
}

}  // namespace net_rx
