# HYPEROSCI UNO-Q Controller Architecture

**Date:** 2026-02-03  
**Status:** Architecture Design  
**Document Version:** 1.0

---

## 1. System Overview

The Arduino UNO Q serves as the central controller for the HYPEROSCI system, eliminating the need for a laptop during performances.

### 1.1 High-Level Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │         MOBILE DEVICE               │
                                    │     (Phone/Tablet Browser)          │
                                    │                                     │
                                    │  ┌─────────────────────────────┐   │
                                    │  │   Web Control Interface     │   │
                                    │  │   - Visualization presets   │   │
                                    │  │   - Mode switching          │   │
                                    │  │   - Parameter adjustment    │   │
                                    │  └─────────────────────────────┘   │
                                    └──────────────┬──────────────────────┘
                                                   │ WiFi (HTTP/WebSocket)
                                                   │
┌──────────────────────────────────────────────────┼──────────────────────────────────────────────────┐
│                                   ARDUINO UNO Q (Controller)                                        │
│                                                  │                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                            Qualcomm QRB2210 (Debian Linux)                                   │  │
│  │                                                                                              │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐│  │
│  │  │  osci-render    │  │   Audio Input   │  │   Web Server    │  │   Network Manager       ││  │
│  │  │  (headless)     │  │   Processing    │  │   (Flask/Node)  │  │   - WiFi AP mode        ││  │
│  │  │                 │  │                 │  │                 │  │   - UDP multicast       ││  │
│  │  │  - SVG/OBJ      │  │  - USB Mic      │  │  - REST API     │  │   - Slave discovery     ││  │
│  │  │  - Lua scripts  │  │  - FFT analysis │  │  - WebSocket    │  │   - Time sync (NTP)     ││  │
│  │  │  - Effects      │  │  - Beat detect  │  │  - Web UI       │  │                         ││  │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────┘│  │
│  │           │                    │                    │                        │             │  │
│  │           └────────────────────┴────────────────────┴────────────────────────┘             │  │
│  │                                           │                                                │  │
│  │                                    Audio Stream                                            │  │
│  │                                    (X,Y samples)                                           │  │
│  │                                           │                                                │  │
│  └───────────────────────────────────────────┼────────────────────────────────────────────────┘  │
│                                              │ RPC Bridge                                        │
│  ┌───────────────────────────────────────────┼────────────────────────────────────────────────┐  │
│  │                            STM32U585 (Real-time MCU)                                       │  │
│  │                                           │                                                │  │
│  │                            ┌──────────────┴──────────────┐                                │  │
│  │                            │    Network TX Buffer        │                                │  │
│  │                            │    (DMA to WiFi)            │                                │  │
│  │                            └──────────────┬──────────────┘                                │  │
│  │                                           │                                                │  │
│  └───────────────────────────────────────────┼────────────────────────────────────────────────┘  │
│                                              │                                                   │
└──────────────────────────────────────────────┼───────────────────────────────────────────────────┘
                                               │
                          WiFi AP (5GHz preferred)
                          UDP Multicast: 239.0.0.1:5000
                                               │
            ┌──────────────────┬───────────────┼───────────────┬──────────────────┐
            │                  │               │               │                  │
            ▼                  ▼               ▼               ▼                  ▼
    ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
    │   SLAVE 1     │  │   SLAVE 2     │  │   SLAVE 3     │  │   SLAVE 4     │
    │ ESP32-C3 +    │  │ ESP32-C3 +    │  │ ESP32-C3 +    │  │ ESP32-C3 +    │
    │ PCM5102A      │  │ PCM5102A      │  │ PCM5102A      │  │ PCM5102A      │
    │               │  │               │  │               │  │               │
    │ + MAX4466 Mic │  │ + MAX4466 Mic │  │ + MAX4466 Mic │  │ + MAX4466 Mic │
    └───────┬───────┘  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘
            │                  │               │               │
            ▼                  ▼               ▼               ▼
    ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
    │ Oscilloscope 1│  │ Oscilloscope 2│  │ Oscilloscope 3│  │ Oscilloscope 4│
    └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘
```

### 1.2 Key Benefits

| Benefit | Description |
|---------|-------------|
| **No Laptop Required** | Fully standalone system |
| **Mobile Control** | Adjust settings from phone via web interface |
| **Self-Contained WiFi** | UNO Q acts as access point |
| **Audio-Reactive** | Built-in mic for music visualization |
| **Centralized Processing** | All rendering on one powerful device |
| **Easy Updates** | Upload new presets/scripts via web UI |

---

## 2. UNO-Q Software Architecture

### 2.1 Linux System Components

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         DEBIAN LINUX (Qualcomm QRB2210)                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                              SYSTEMD SERVICES                                ││
│  ├─────────────────────────────────────────────────────────────────────────────┤│
│  │                                                                              ││
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  ││
│  │  │ hyperosci-core   │  │ hyperosci-web    │  │ hyperosci-network        │  ││
│  │  │                  │  │                  │  │                          │  ││
│  │  │ - osci-render    │  │ - Flask/FastAPI  │  │ - hostapd (WiFi AP)     │  ││
│  │  │ - Audio capture  │  │ - WebSocket      │  │ - dnsmasq (DHCP)        │  ││
│  │  │ - Stream output  │  │ - Static files   │  │ - Slave discovery       │  ││
│  │  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                           SHARED RESOURCES                                   ││
│  ├─────────────────────────────────────────────────────────────────────────────┤│
│  │                                                                              ││
│  │  /var/hyperosci/                                                            ││
│  │  ├── presets/           # Saved visualization presets                       ││
│  │  ├── scripts/           # Lua scripts for osci-render                       ││
│  │  ├── assets/            # SVG, OBJ files                                    ││
│  │  ├── config.json        # System configuration                              ││
│  │  └── state.json         # Runtime state (current preset, etc.)              ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Core Service: hyperosci-core

The main rendering and streaming service.

```python
# Pseudocode structure for hyperosci-core

class HyperosciCore:
    def __init__(self):
        self.osci_renderer = OsciRenderer()      # osci-render integration
        self.audio_input = AudioInput()          # USB microphone
        self.audio_analyzer = AudioAnalyzer()    # FFT, beat detection
        self.network_streamer = NetworkStreamer() # UDP multicast
        self.config = Config.load()
        
    def run(self):
        while True:
            # 1. Get visualization frame from osci-render
            frame = self.osci_renderer.render_frame()
            
            # 2. Apply audio modulation if enabled
            if self.config.audio_reactive:
                audio_data = self.audio_input.read()
                analysis = self.audio_analyzer.analyze(audio_data)
                frame = self.apply_audio_modulation(frame, analysis)
            
            # 3. Stream to all slaves
            self.network_streamer.send(frame)
            
    def apply_audio_modulation(self, frame, analysis):
        # Modulate visualization based on audio
        # - Scale by volume
        # - Apply frequency-based effects
        # - Trigger on beats
        pass

class NetworkStreamer:
    MULTICAST_GROUP = "239.0.0.1"
    MULTICAST_PORT = 5000
    
    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        
    def send(self, audio_samples):
        packet = AudioPacket(
            timestamp=get_sync_time(),
            samples=audio_samples
        )
        self.socket.sendto(
            packet.serialize(),
            (self.MULTICAST_GROUP, self.MULTICAST_PORT)
        )
```

### 2.3 Web Service: hyperosci-web

REST API and web interface for mobile control.

```python
# Flask/FastAPI web service structure

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# === REST API Endpoints ===

@app.get("/api/status")
async def get_status():
    """Get system status including connected slaves"""
    return {
        "mode": current_mode,           # "osci-render" | "local-mic"
        "preset": current_preset,
        "slaves": [
            {"id": 1, "status": "connected", "ip": "192.168.4.2"},
            {"id": 2, "status": "connected", "ip": "192.168.4.3"},
            {"id": 3, "status": "connected", "ip": "192.168.4.4"},
            {"id": 4, "status": "connected", "ip": "192.168.4.5"},
        ],
        "audio_reactive": True,
        "sample_rate": 48000
    }

@app.post("/api/mode")
async def set_mode(mode: str):
    """Switch between osci-render and local-mic mode"""
    # mode: "osci-render" | "local-mic" | "hybrid"
    pass

@app.get("/api/presets")
async def list_presets():
    """List available visualization presets"""
    pass

@app.post("/api/presets/{preset_id}/activate")
async def activate_preset(preset_id: str):
    """Activate a visualization preset"""
    pass

@app.post("/api/effects/{effect_id}")
async def set_effect_parameter(effect_id: str, value: float):
    """Adjust effect parameters in real-time"""
    pass

@app.post("/api/slaves/command")
async def send_slave_command(command: dict):
    """Send command to slaves (e.g., switch to local mic mode)"""
    pass

# === WebSocket for Real-time Updates ===

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time status updates and control"""
    await websocket.accept()
    while True:
        # Send status updates
        await websocket.send_json(get_realtime_status())
        # Receive commands
        data = await websocket.receive_json()
        handle_command(data)

# === Static Web UI ===

app.mount("/", StaticFiles(directory="web-ui", html=True), name="static")
```

### 2.4 Web UI Design

Mobile-first web interface accessible at `http://192.168.4.1` when connected to UNO-Q AP.

```
┌─────────────────────────────────────────────────────────────────┐
│                    HYPEROSCI Control                        ≡   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              SYSTEM STATUS                               │   │
│  │  ●  All 4 oscilloscopes connected                       │   │
│  │  🎵 Audio reactive: ON                                  │   │
│  │  📶 WiFi: HYPEROSCI_AP (4 clients)                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              MODE SELECT                                 │   │
│  │                                                          │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │   │
│  │  │              │  │              │  │              │  │   │
│  │  │  🎨 OSCI     │  │  🎤 LOCAL    │  │  🔀 HYBRID  │  │   │
│  │  │  RENDER     │  │  MIC         │  │              │  │   │
│  │  │  [ACTIVE]   │  │              │  │              │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              PRESETS                                     │   │
│  │                                                          │   │
│  │  [Circle Wave]  [Lissajous]  [Cube 3D]  [Custom 1]     │   │
│  │                                                          │   │
│  │  [Text Scroll]  [Audio Viz]  [Spiral]   [+ New]        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              PARAMETERS                                  │   │
│  │                                                          │   │
│  │  Frequency      ████████████░░░░░░░░░░  220 Hz          │   │
│  │  Rotation       ██████░░░░░░░░░░░░░░░░  0.3             │   │
│  │  Audio React    ████████████████░░░░░░  0.8             │   │
│  │  Wobble         ████░░░░░░░░░░░░░░░░░░  0.2             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              PER-SCOPE OVERRIDE                          │   │
│  │                                                          │   │
│  │  Scope 1: [Network] ▼    Scope 2: [Network] ▼          │   │
│  │  Scope 3: [Network] ▼    Scope 4: [Local Mic] ▼        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Network Architecture

### 3.1 WiFi Access Point Configuration

The UNO-Q runs as a WiFi access point, eliminating the need for a travel router.

```bash
# /etc/hostapd/hostapd.conf

interface=wlan0
driver=nl80211
ssid=HYPEROSCI_AP
hw_mode=a           # 5GHz for lower latency
channel=36
wmm_enabled=1
country_code=NL
ieee80211n=1
ieee80211ac=1

# Security
wpa=2
wpa_passphrase=hyperosci2026
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
```

```bash
# /etc/dnsmasq.conf

interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/hyperosci.local/192.168.4.1
```

### 3.2 Network Topology

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           HYPEROSCI NETWORK                                  │
│                                                                              │
│  SSID: HYPEROSCI_AP                                                         │
│  Band: 5 GHz (802.11ac)                                                     │
│  Subnet: 192.168.4.0/24                                                     │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │                                                                          ││
│  │   ┌───────────────────┐                                                 ││
│  │   │    UNO-Q (AP)     │                                                 ││
│  │   │   192.168.4.1     │                                                 ││
│  │   │                   │                                                 ││
│  │   │  - Web UI: :80    │                                                 ││
│  │   │  - API: :8080     │                                                 ││
│  │   │  - Stream: :5000  │                                                 ││
│  │   └─────────┬─────────┘                                                 ││
│  │             │                                                           ││
│  │   ┌─────────┴─────────┬─────────────────┬─────────────────┐            ││
│  │   │                   │                 │                 │            ││
│  │   ▼                   ▼                 ▼                 ▼            ││
│  │ ┌─────────┐     ┌─────────┐       ┌─────────┐       ┌─────────┐       ││
│  │ │ Slave 1 │     │ Slave 2 │       │ Slave 3 │       │ Slave 4 │       ││
│  │ │ .4.2    │     │ .4.3    │       │ .4.4    │       │ .4.5    │       ││
│  │ └─────────┘     └─────────┘       └─────────┘       └─────────┘       ││
│  │                                                                         ││
│  │   ┌─────────┐                                                          ││
│  │   │ Phone   │  (Control interface)                                     ││
│  │   │ .4.10   │                                                          ││
│  │   └─────────┘                                                          ││
│  │                                                                          ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Communication Protocol

#### Audio Streaming (UDP Multicast)

```
┌───────────────────────────────────────────────────────────────────┐
│                    AUDIO PACKET FORMAT                            │
├───────────────────────────────────────────────────────────────────┤
│ Offset │ Size   │ Field          │ Description                    │
├────────┼────────┼────────────────┼────────────────────────────────┤
│ 0      │ 4      │ Magic          │ 0x48595045 ("HYPE")           │
│ 4      │ 4      │ Sequence       │ Packet sequence number        │
│ 8      │ 8      │ Timestamp      │ Playback time (µs since boot) │
│ 16     │ 2      │ Flags          │ Bit 0: sync pulse             │
│ 18     │ 2      │ Sample Count   │ Number of stereo samples      │
│ 20     │ N×4    │ Audio Data     │ Interleaved L/R int16 samples │
└───────────────────────────────────────────────────────────────────┘
```

#### Control Commands (UDP Unicast to Slaves)

```json
// Command: Switch slave to local mic mode
{
    "cmd": "set_mode",
    "mode": "local_mic",
    "params": {
        "lowpass_freq": 200
    }
}

// Command: Switch slave to network mode
{
    "cmd": "set_mode",
    "mode": "network"
}

// Command: Sync time
{
    "cmd": "sync",
    "server_time": 1234567890123
}
```

---

## 4. Audio Processing Pipeline

### 4.1 osci-render Integration

Options for running osci-render on UNO-Q:

#### Option A: Native Build (Recommended)

Compile osci-render from source for ARM64 Linux.

```bash
# Build dependencies
sudo apt install build-essential libasound2-dev libjack-jackd2-dev \
    libfreetype6-dev libx11-dev libxcomposite-dev libxcursor-dev \
    libxext-dev libxinerama-dev libxrandr-dev libxrender-dev \
    libglu1-mesa-dev mesa-common-dev

# Clone and build
git clone https://github.com/jameshball/osci-render.git
cd osci-render
# ... build with JUCE for ARM64
```

#### Option B: Lightweight Custom Renderer

If full osci-render is too heavy, create a lightweight alternative:

```python
# hyperosci-render: Lightweight osci-render alternative
# Focused on essential features for live performance

class LightweightRenderer:
    def __init__(self):
        self.shapes = []
        self.effects = []
        self.sample_rate = 48000
        
    def load_svg(self, path):
        """Load and parse SVG to path points"""
        pass
        
    def load_lua(self, script):
        """Load Lua script for custom shapes"""
        pass
        
    def render_frame(self):
        """Generate audio samples for current frame"""
        samples = []
        for t in range(self.samples_per_frame):
            x, y = self.trace_path(t / self.sample_rate)
            x, y = self.apply_effects(x, y)
            samples.append((x, y))
        return samples
```

### 4.2 Audio Input Processing

```python
class AudioAnalyzer:
    def __init__(self, sample_rate=48000, fft_size=2048):
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        
    def analyze(self, audio_buffer):
        # Compute FFT
        spectrum = np.fft.rfft(audio_buffer)
        magnitudes = np.abs(spectrum)
        
        # Extract features
        return {
            "volume": np.sqrt(np.mean(audio_buffer**2)),  # RMS
            "bass": np.mean(magnitudes[0:10]),            # Low frequencies
            "mids": np.mean(magnitudes[10:100]),          # Mid frequencies
            "highs": np.mean(magnitudes[100:]),           # High frequencies
            "peak_freq": np.argmax(magnitudes) * self.sample_rate / self.fft_size,
            "is_beat": self.detect_beat(magnitudes)
        }
        
    def detect_beat(self, magnitudes):
        # Simple beat detection based on bass energy
        bass_energy = np.mean(magnitudes[0:10])
        if bass_energy > self.beat_threshold * 1.5:
            self.beat_threshold = bass_energy
            return True
        self.beat_threshold = self.beat_threshold * 0.95 + bass_energy * 0.05
        return False
```

---

## 5. Slave Communication Protocol

### 5.1 Slave Discovery

```python
# UNO-Q: Discover slaves via mDNS
import zeroconf

class SlaveDiscovery:
    SERVICE_TYPE = "_hyperosci._udp.local."
    
    def __init__(self):
        self.zeroconf = zeroconf.Zeroconf()
        self.slaves = {}
        
    def start(self):
        browser = zeroconf.ServiceBrowser(
            self.zeroconf, 
            self.SERVICE_TYPE, 
            self
        )
        
    def add_service(self, zc, type, name):
        info = zc.get_service_info(type, name)
        slave_id = int(name.split("-")[1])
        self.slaves[slave_id] = {
            "ip": socket.inet_ntoa(info.addresses[0]),
            "port": info.port,
            "name": name
        }
        print(f"Discovered slave {slave_id} at {self.slaves[slave_id]['ip']}")
```

```cpp
// ESP32-C3: Advertise via mDNS
#include <ESPmDNS.h>

void setup_mdns(int slave_id) {
    char hostname[32];
    sprintf(hostname, "hyperosci-slave-%d", slave_id);
    
    MDNS.begin(hostname);
    MDNS.addService("_hyperosci", "_udp", 5000);
}
```

### 5.2 Time Synchronization

```python
# UNO-Q: NTP-like sync master
class SyncMaster:
    def __init__(self):
        self.boot_time = time.monotonic_ns()
        
    def get_sync_time(self):
        """Microseconds since boot"""
        return (time.monotonic_ns() - self.boot_time) // 1000
        
    def send_sync_packet(self):
        """Send sync packet to all slaves"""
        packet = {
            "type": "sync",
            "t1": self.get_sync_time()  # Send time
        }
        multicast_send(packet)
```

```cpp
// ESP32-C3: Sync slave
class SyncSlave {
    int64_t offset = 0;  // Offset from master time
    
    void handle_sync_packet(SyncPacket& pkt) {
        int64_t t1 = pkt.t1;           // Master send time
        int64_t t2 = get_local_time(); // Our receive time
        
        // Simple offset calculation (assumes symmetric delay)
        // For better accuracy, implement full NTP algorithm
        offset = t1 - t2;
    }
    
    int64_t get_sync_time() {
        return get_local_time() + offset;
    }
};
```

---

## 6. Configuration Management

### 6.1 System Configuration

```json
// /var/hyperosci/config.json
{
    "system": {
        "sample_rate": 48000,
        "buffer_size_ms": 50,
        "wifi_channel": 36,
        "wifi_ssid": "HYPEROSCI_AP",
        "wifi_password": "hyperosci2026"
    },
    "audio": {
        "input_device": "USB Audio Device",
        "input_gain": 1.0,
        "audio_reactive": true,
        "beat_sensitivity": 0.7
    },
    "renderer": {
        "default_frequency": 220,
        "default_preset": "circle_wave"
    },
    "slaves": {
        "default_mode": "network",
        "fallback_mode": "local_mic",
        "connection_timeout_ms": 1000
    }
}
```

### 6.2 Preset Format

```json
// /var/hyperosci/presets/circle_wave.json
{
    "name": "Circle Wave",
    "description": "Simple circle with audio-reactive scaling",
    "type": "builtin",
    "renderer": {
        "shape": "circle",
        "frequency": 220,
        "effects": [
            {
                "type": "scale",
                "base": 1.0,
                "audio_mod": {
                    "source": "volume",
                    "amount": 0.5
                }
            },
            {
                "type": "rotate",
                "speed": 0.1
            }
        ]
    }
}
```

---

## 7. Implementation Phases

### Phase 1: ESP32-C3 + PCM5102A Slaves (Weeks 1-8)
*Already planned - core system*

| Week | Task |
|------|------|
| 1-2 | ESP32-C3 I2S + PCM5102A hardware test |
| 3-4 | Firmware: local mic mode (fallback) |
| 5-6 | Firmware: network receive mode |
| 7-8 | Test with PC streaming (before UNO-Q) |

### Phase 2: UNO-Q Controller (Weeks 9-14)

| Week | Task |
|------|------|
| 9 | UNO-Q Debian setup, WiFi AP configuration |
| 10 | osci-render build/port to ARM64 |
| 11 | Network streamer implementation |
| 12 | Web server + REST API |
| 13 | Web UI (mobile-first) |
| 14 | Audio input + reactive features |

### Phase 3: Integration & Testing (Weeks 15-16)

| Week | Task |
|------|------|
| 15 | Full system integration |
| 16 | Performance testing, bug fixes, venue rehearsal |

---

## 8. Hardware Connections

### 8.1 UNO-Q Setup

```
┌─────────────────────────────────────────────────────────────────────┐
│                       ARDUINO UNO-Q                                  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    USB-C PORT                                   │ │
│  │                        │                                        │ │
│  │          ┌─────────────┼─────────────┐                         │ │
│  │          │             │             │                         │ │
│  │          ▼             ▼             ▼                         │ │
│  │    ┌──────────┐  ┌──────────┐  ┌──────────┐                   │ │
│  │    │ USB Mic  │  │ Power    │  │ Debug    │                   │ │
│  │    │ (audio)  │  │ (5V/3A)  │  │ Console  │                   │ │
│  │    └──────────┘  └──────────┘  └──────────┘                   │ │
│  │                                                                 │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  WiFi Antenna: Built-in (5GHz 802.11ac)                             │
│  No external connections needed for HYPEROSCI controller!           │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.2 Full System Power

```
┌─────────────────────────────────────────────────────────────────────┐
│                       POWER ARCHITECTURE                             │
│                                                                      │
│  UNO-Q Controller:                                                  │
│  └── USB-C 5V/3A adapter (wall power recommended for performance)   │
│                                                                      │
│  Slaves (×4):                                                       │
│  └── Each: LiPo 3.7V 1000-2000mAh + TP4056                         │
│      └── Battery life: ~8-10 hours per slave                       │
│                                                                      │
│  Optional: Portable power bank for UNO-Q during performance         │
│  └── 20000mAh @ 5V/3A ≈ 3-4 hours runtime                          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| osci-render won't build on ARM64 | Medium | High | Fall back to lightweight custom renderer |
| UNO-Q WiFi AP performance | Low | Medium | Test early; fall back to travel router |
| Debian resource constraints | Low | Medium | Optimize services, disable unused components |
| Web UI too slow on mobile | Low | Low | Keep UI simple, use WebSocket for updates |
| Time sync drift | Medium | Medium | Periodic re-sync every second |

---

## 10. Future Enhancements

1. **DMX Output** - Control stage lighting from UNO-Q
2. **OSC Input** - Accept Open Sound Control for external control
3. **Recording** - Record performances for playback
4. **Multiple Scenes** - Quick scene switching during performance
5. **MIDI Input** - Control via MIDI controller
6. **Backup/Restore** - Easy configuration backup

---

## 11. Summary

The Arduino UNO-Q transforms HYPEROSCI from a PC-dependent system to a fully standalone, portable performance platform:

| Feature | Before (PC) | After (UNO-Q) |
|---------|-------------|---------------|
| Controller | Laptop required | UNO-Q (pocket-sized) |
| WiFi | External router | Built-in AP |
| Control | Desktop app | Mobile web UI |
| Portability | Low | High |
| Setup time | Minutes | Seconds |
| Dependencies | Many | Self-contained |

**Total system:**
- 1× Arduino UNO-Q (€65) - Controller
- 4× ESP32-C3 + PCM5102A (~€24) - Slaves
- 4× MAX4466 + LiPo + TP4056 (already have)
- 1× USB microphone for UNO-Q (~€10-20)

**Estimated total new cost:** ~€100-120
