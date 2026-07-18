// HYPEROSCI wire protocol v1.
// Normative prose spec: docs/protocol.md. This header is shared verbatim with
// the UNO-Q controller (C/C++); keep it dependency-free.
// All multi-byte fields little-endian (native on both ESP32-C3 and QRB2210).
#pragma once

#include <stdint.h>

#define HYPE_PROTO_VERSION 1

// "HYPE" on the wire: bytes 0x48 0x59 0x50 0x45. As a little-endian uint32
// read from those bytes, the value is 0x45505948.
#define HYPE_MAGIC 0x45505948u

enum HypePacketType : uint8_t {
  HYPE_AUDIO = 0x01,     // controller -> slaves, port 5000
  HYPE_SYNC = 0x02,      // controller -> slaves, port 5001, every 500 ms
  HYPE_CMD = 0x03,       // controller -> slave, port 5001, JSON payload
  HYPE_STATUS = 0x04,    // slave -> controller, port 5002, every 1 s
};

// Header flags
#define HYPE_FLAG_SYNC_PULSE 0x0001 // audio packet marks a resync point

typedef struct __attribute__((packed)) {
  uint32_t magic;        // HYPE_MAGIC
  uint8_t version;       // HYPE_PROTO_VERSION
  uint8_t type;          // HypePacketType
  uint16_t flags;
  uint32_t seq;          // per-type monotonically increasing
  uint64_t timestamp_us; // controller monotonic clock, see docs/protocol.md §4
} HypeHeader;            // 20 bytes

// --- HYPE_AUDIO payload -----------------------------------------------------
// timestamp_us = playback deadline (controller clock) of the FIRST frame.
typedef struct __attribute__((packed)) {
  uint32_t sample_rate;  // 48000 in v1; slaves drop packets with other rates
  uint16_t frame_count;  // stereo frames that follow (240 in v1)
  uint16_t reserved;
  // int16_t samples[frame_count * 2];  L=X first, then R=Y, interleaved
} HypeAudioPayload;      // 8 bytes + samples

#define HYPE_AUDIO_FRAMES 240 // 5 ms at 48 kHz -> 20+8+960 = 988 B/packet

// --- HYPE_SYNC payload ------------------------------------------------------
// timestamp_us = controller clock at (approximate) moment of transmission.
// Slaves compute offset = timestamp_us - local_us_at_rx and smooth it.
// v1 deliberately ignores flight time (<2 ms on one WiFi hop; tolerance is
// +-5 ms). No payload beyond the header.

// --- HYPE_CMD payload -------------------------------------------------------
// UTF-8 JSON, NOT null-terminated (length = UDP payload length - header).
// Commands (v1):
//   {"cmd":"set_mode","mode":"network"|"local"|"hybrid"}
//   {"cmd":"identify"}              -> slave blinks both LEDs for 3 s
//   {"cmd":"set_gain","gain":0.0..1.0}
//   {"cmd":"reboot"}

// --- HYPE_STATUS payload ----------------------------------------------------
typedef struct __attribute__((packed)) {
  uint8_t mac[6];        // slave identity
  uint8_t slave_id;      // default: MAC last octet; console-overridable
  uint8_t mode;          // 0=LOCAL 1=NETWORK 2=HYBRID (active mode)
  uint8_t source;        // 0=local render 1=network stream (what's playing)
  int8_t rssi_dbm;
  uint16_t vbat_mv;
  uint16_t buffer_depth_frames;
  uint32_t rx_packets;
  uint32_t rx_dropped;   // late/stale/overflow discards
  uint32_t underruns;
  uint32_t uptime_s;
  int32_t clock_offset_us; // current smoothed sync offset (saturated)
  uint8_t local_pattern;   // renderer_local::Pattern: 0=mic 1=circle
                           // 2=lissajous 3=ramp 4=square (drawn when source=0)
} HypeStatusPayload;     // 35 bytes

_Static_assert(sizeof(HypeHeader) == 20, "header must be 20 bytes");
_Static_assert(sizeof(HypeStatusPayload) == 35, "status must be 35 bytes");
