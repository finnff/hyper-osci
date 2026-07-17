// Slave side of the SYNC protocol (docs/protocol.md §4): track the offset
// between the controller's monotonic clock and ours from SYNC beacons.
// v1 ignores flight time (<2 ms one WiFi hop, budget is ±5 ms).
#pragma once

#include <stdint.h>

namespace timesync {

void on_beacon(uint64_t master_ts_us);  // called from the network task
bool valid();                           // beacon seen within SYNC_STALE_MS
uint64_t master_now_us();  // local monotonic + offset (raw local if never synced)
int64_t offset_us();
void reset();

}  // namespace timesync
