# Bluetooth Call Bridge for Raspberry Pi

This package creates a **userspace Bluetooth call bridge** for a Raspberry Pi:

- **Phone call downlink**: phone -> Bluetooth HFP/HSP -> Raspberry Pi -> **AUX / headphone jack**
- **Phone call uplink**: **USB webcam microphone** -> Raspberry Pi -> Bluetooth HFP/HSP -> phone
- **Alternate call uplink**: **looped local audio file** -> Raspberry Pi -> Bluetooth HFP/HSP -> phone
- **Optional uplink tap**: mirror the uplink PCM stream to a file, FIFO, or command for future real-time integrations

The SSH terminal app installed by this package is named **`tyrionTalks`**.

It is designed for **Raspberry Pi 3 / Raspberry Pi OS Bookworm or Debian 12-class systems** and uses:

- **BlueZ** for Bluetooth pairing/connection
- **BlueALSA** for HFP/HSP audio profile exposure to ALSA
- **ALSA tools** (`arecord`, `aplay`) for the actual audio bridge

---

## Important scope

This package is intentionally built as a **host-side Linux solution**, not custom Broadcom firmware.

That is the right layer for this problem:

- Raspberry Pi onboard Bluetooth controllers already expose the HCI/SCO transport needed for headset/hands-free audio.
- On Linux, **BlueZ + BlueALSA** are the practical place to implement HFP/HSP audio routing.

---

## What this package does

When the bridge is running:

1. It configures the Pi Bluetooth adapter to be pairable/discoverable.
2. It starts the BlueALSA daemon in **headset / hands-free target mode**:
   - `hfp-hf`
   - `hsp-hs`
3. It starts a **downlink path**:
   - Bluetooth SCO call audio -> `bluealsa-aplay` -> configured AUX ALSA PCM
4. It starts an **uplink path**:
   - configured webcam mic ALSA PCM -> `aplay` to BlueALSA `PROFILE=sco`
5. It can optionally mirror the uplink PCM stream to:
   - a file
   - a FIFO
   - another command on stdin
6. It restarts the audio workers if they exit.

The uplink worker can run in three modes:

- `UPLINK_SOURCE=mic` uses the configured `MIC_PCM`
- `UPLINK_SOURCE=file` decodes and loops `UPLINK_AUDIO_FILE`
- `UPLINK_SOURCE=soundboard` watches a selector file so you can hot-swap the active looping clip

The included SSH-friendly terminal UI can:

- select the paired phone / phone MAC
- select the playback output device
- switch between microphone, file, and soundboard uplink modes
- choose the microphone PCM, loop file, or active soundboard clip
- configure an uplink stream tap for future transcription or streaming services

---

## What it does **not** do

This package does **not** currently:

- auto-answer phone calls
- force the phone OS to route ringing/call audio over Bluetooth if the phone refuses
- provide GUI management
- do DSP/noise suppression/echo cancellation
- mix music into the microphone uplink
- replace telephony policy inside Android/iOS
- perform transcription by itself (it only exposes the uplink stream for another service to consume)

In practice, you normally:

- pair the phone to the Pi once
- answer the incoming call on the phone or the Bluetooth route
- let the Pi carry the audio path once the phone acquires the HFP/HSP transport

---

## Package layout

```text
bt-call-bridge-package/
├── README.md
├── bridge.conf.example
├── bridge.conf
├── install.sh
├── pair_phone.sh
├── start.sh
├── terminal.sh
├── stop.sh
├── audio/
│   ├── KBC_PRANK.mp3
│   └── SAMARTH-WR-PRANK.mp3
├── scripts/
│   ├── bt_call_bridge.py
│   ├── bt_call_bridge_terminal.py
│   └── pcm_stream_fanout.py
└── systemd/
    └── bt-call-bridge.service
```

---

## Prerequisites

### Hardware

- Raspberry Pi with Bluetooth (Pi 3 is fine)
- Wired speaker / AUX-connected output on the Pi
- USB webcam or USB microphone attached to the Pi
- Phone that supports Bluetooth HFP/HSP headset routing

### Software

The installer uses these packages:

- `bluez`
- `bluez-alsa-utils`
- `libasound2-plugin-bluez`
- `alsa-utils`
- `ffmpeg`
- `python3`

---

## Installation

### Option A — install in place

Unzip the package on the Pi and run:

```bash
sudo ./install.sh
```

This installs the files to:

```bash
/opt/bt-call-bridge
```

It also installs a global launcher:

```bash
/usr/local/bin/tyrionTalks
```

and creates an audio library directory used by the loop-file picker. The bridge
looks in the package's own `audio/` directory by default, so after install that
path is:

```bash
/opt/bt-call-bridge/audio
```

and installs the included systemd unit:

```bash
/etc/systemd/system/bt-call-bridge.service
```

During install, it also:

- disables conflicting system Bluetooth audio services such as `bluealsa`, `bluealsa-aplay`, `bt-speaker-agent`, and `ofono`
- forces headset-style pairing mode with `btmgmt io-cap 0x03` and `BT_AGENT_CAPABILITY=NoInputNoOutput`

After install, you can open the interface from **anywhere** in the terminal with:

```bash
tyrionTalks
```

If you want to point it at a different config file or audio directory:

```bash
tyrionTalks /path/to/bridge.conf /path/to/audio-dir
```

### Option B — run directly without installing systemd

You can also keep the folder anywhere and run:

```bash
sudo ./terminal.sh ./bridge.conf
```

When running directly from the repo, the default loop-file library is the
package-local `./audio` directory.

From the terminal UI, save your selections, pair the phone, and launch the bridge in the foreground.

---

## Find your ALSA device names

Before editing the config, identify your playback and microphone devices.

### List playback devices

```bash
aplay -l
```

Example output on Pi often includes something like:

```text
card 1: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones
```

That maps nicely to:

```bash
AUX_PCM=plughw:CARD=Headphones,DEV=0
```

### List capture devices

```bash
arecord -l
```

Example webcam mic output:

```text
card 2: C920 [HD Pro Webcam C920], device 0: USB Audio [USB Audio]
```

That maps to something like:

```bash
MIC_PCM=plughw:CARD=C920,DEV=0
```

### Helper command

You can also use the bundled helper:

```bash
python3 scripts/bt_call_bridge.py --list-alsa
```

To list remembered Bluetooth devices:

```bash
python3 scripts/bt_call_bridge.py --list-bt-devices
```

### SSH terminal interface

After installing with `sudo ./install.sh`, the easiest way to operate this package over SSH is:

```bash
tyrionTalks
```

If you are running directly from the unpacked folder without installing, use:

```bash
sudo ./terminal.sh ./bridge.conf
```

That menu lets you select:

- the phone MAC / paired device
- the AUX/headphone output device
- the uplink mode (`mic` or `file`)
- the microphone PCM or audio file
- an optional tap target for the uplink PCM stream

The loop-file picker reads from the dedicated audio library directory by default, and the tap is useful when you later want to forward live microphone audio into another process for transcription.

---

## Configure the bridge

Copy the example config if needed:

```bash
cp bridge.conf.example bridge.conf
```

Edit:

```bash
nano bridge.conf
```

### Example config

```ini
PHONE_MAC=AA:BB:CC:DD:EE:FF
BT_ALIAS=PiCallBridge
BT_HCI=hci0
AUX_PCM=plughw:CARD=Headphones,DEV=0
MIC_PCM=plughw:CARD=C920,DEV=0
UPLINK_SOURCE=mic
UPLINK_AUDIO_FILE=audio/KBC_PRANK.mp3
SCO_RATE=16000
UPLINK_TAP_MODE=off
UPLINK_TAP_PATH=/tmp/bt-call-bridge-uplink.pcm
UPLINK_TAP_COMMAND=
AUTO_CONNECT=1
DISCOVERABLE=1
PAIRABLE=1
PAIRABLE_TIMEOUT=0
DISCOVERABLE_TIMEOUT=0
BLUEALSA_INITIAL_VOLUME=70
BLUEALSA_KEEP_ALIVE=-1
BLUEALSA_IO_RT_PRIORITY=20
ENABLE_A2DP_SINK=0
BLUEALSA_EXTRA_ARGS=
RESTART_DELAY_SECS=2
```

### Notes on the main settings

#### `PHONE_MAC`
The paired phone's Bluetooth MAC address.

Leave it blank the first time if you have not paired yet.

#### `AUX_PCM`
The ALSA playback device that should receive phone call audio.

#### `MIC_PCM`
The ALSA capture device for the microphone that should be sent to the phone.

#### `UPLINK_SOURCE`
Choose how uplink audio is generated:

- `mic` captures from `MIC_PCM`
- `file` loops the file configured in `UPLINK_AUDIO_FILE`
- `soundboard` uses `UPLINK_AUDIO_FILE` as a fallback clip and watches `SOUNDBOARD_SELECTOR_PATH` for live clip changes

#### `UPLINK_AUDIO_FILE`
Local audio file to feed into the call when `UPLINK_SOURCE=file`.
It is also the fallback/default clip for `UPLINK_SOURCE=soundboard`.

Relative paths are resolved relative to the directory containing `bridge.conf`.
By default, place your loop files inside the `audio/` directory and choose them from the `tyrionTalks` menu.

#### `SOUNDBOARD_SELECTOR_PATH`
Text file used by `UPLINK_SOURCE=soundboard`.

The terminal UI writes the currently selected clip here, and the running bridge hot-swaps to that clip without needing a full bridge restart.

#### `UPLINK_TAP_MODE`
Choose whether the uplink PCM stream should also be copied somewhere besides the phone call path.

- `off` keeps the bridge behavior unchanged
- `file` appends raw PCM to `UPLINK_TAP_PATH`
- `command` pipes raw PCM to `UPLINK_TAP_COMMAND` on stdin

#### `UPLINK_TAP_PATH`
File or FIFO target for `UPLINK_TAP_MODE=file`.

This is handy for debugging or for handing the stream to another local service through a named pipe.

#### `UPLINK_TAP_COMMAND`
Command to launch when `UPLINK_TAP_MODE=command`.

The command receives raw mono 16-bit PCM on stdin at `SCO_RATE`. The bridge also exports:

- `BT_BRIDGE_STREAM_SAMPLE_RATE`
- `BT_BRIDGE_STREAM_CHANNELS`
- `BT_BRIDGE_STREAM_SAMPLE_FORMAT`

#### `SCO_RATE`
Use `16000` by default.

Why:

- HFP wideband (`mSBC`) prefers 16 kHz audio
- if the phone falls back to CVSD / narrowband, BlueALSA's `bluealsa` PCM can still down-convert as needed

#### `BLUEALSA_KEEP_ALIVE`
`-1` keeps transports alive aggressively and is useful for reconnect-style embedded setups.

#### `BLUEALSA_IO_RT_PRIORITY`
Helps the I/O thread behave better with low-latency SCO audio.

---


## Your detected ALSA devices

From your Pi outputs:

```text
Playback: card 1: Headphones [bcm2835 Headphones], device 0
Capture : card 2: C920 [HD Pro Webcam C920], device 0
```

Use these exact values in `bridge.conf`:

```ini
AUX_PCM=plughw:CARD=Headphones,DEV=0
MIC_PCM=plughw:CARD=C920,DEV=0
```

If your phone is the one previously shown by `bluetoothctl devices` as `58:43:AB:D9:8C:6E Samarth`, then you can also set:

```ini
PHONE_MAC=58:43:AB:D9:8C:6E
```

Otherwise leave `PHONE_MAC` blank for first pairing, then fill in the paired phone MAC after you confirm it with `bluetoothctl devices`.

## Pair the phone

If you installed the package, you can do this entire flow from the `tyrionTalks` menu.

### 1) Prepare the adapter for pairing

```bash
sudo ./pair_phone.sh ./bridge.conf
```

This starts a temporary pairing server that:

- powered on
- forces `NoInputNoOutput` pairing at the controller and `bluetoothctl` agent layers
- pairable
- discoverable
- renamed to your configured `BT_ALIAS`
- keeps HFP/HSP call profiles alive while the phone pairs

Keep that terminal open until the phone finishes pairing.

### 2) Pair from the phone

On the phone:

- open Bluetooth settings
- look for the Pi alias (default: `PiCallBridge`)
- pair to it

### 3) Get the phone MAC

On the Pi:

```bash
bluetoothctl devices
```

Example:

```text
Device 58:43:AB:D9:8C:6E MyPhone
```

Copy that into `bridge.conf`:

```ini
PHONE_MAC=58:43:AB:D9:8C:6E
```

---

## Preflight check

Run:

```bash
python3 scripts/bt_call_bridge.py --config ./bridge.conf --preflight
```

This checks that the required commands are present and prints the resolved config values.

---

## Start the bridge

### Manual run

If installed, the most convenient entrypoint is:

```bash
tyrionTalks
```

From that menu you can save config, pair the phone, run preflight, and start the bridge.

### Direct script run

```bash
sudo ./start.sh ./bridge.conf
```

### Stop it

```bash
sudo ./stop.sh
```

### Run as a service

If installed to `/opt/bt-call-bridge` by `install.sh`:

```bash
sudo systemctl enable --now bt-call-bridge.service
sudo systemctl status bt-call-bridge.service
journalctl -u bt-call-bridge.service -f
```

---

## How the audio paths work

### Downlink (phone -> AUX)

This worker is started:

```bash
bluealsa-aplay --profile=SCO --pcm="$AUX_PCM" "$PHONE_MAC"
```

Meaning:

- accept SCO/HFP/HSP audio from the specified phone
- play it to the configured ALSA playback device

### Uplink (webcam mic -> phone)

This worker is started:

```bash
arecord -D "$MIC_PCM" -q -f S16_LE -c 1 -r "$SCO_RATE" \
  | aplay -D "bluealsa:DEV=$PHONE_MAC,PROFILE=sco,HWCOMPAT=silence" -q -f S16_LE -c 1 -r "$SCO_RATE"
```

Meaning:

- capture from the webcam mic
- feed mono PCM audio into the Bluetooth SCO transport
- keep the pipeline tolerant of idle transport states with `HWCOMPAT=silence`

### Alternate uplink (looped local file -> phone)

When `UPLINK_SOURCE=file`, this worker is started instead:

```bash
ffmpeg -hide_banner -loglevel error -nostdin -stream_loop -1 -re -i "$UPLINK_AUDIO_FILE" -vn -f s16le -acodec pcm_s16le -ac 1 -ar "$SCO_RATE" - \
  | aplay -D "bluealsa:DEV=$PHONE_MAC,PROFILE=sco" -q -f S16_LE -c 1 -r "$SCO_RATE"
```

Meaning:

- decode the local audio file
- loop it continuously
- convert it to mono PCM at the SCO rate
- send that audio back to the caller instead of using the live mic

### Alternate uplink (soundboard -> phone)

When `UPLINK_SOURCE=soundboard`, the bridge starts a helper that watches `SOUNDBOARD_SELECTOR_PATH`, loops the selected clip, and restarts the decoder whenever that selected clip changes.

Meaning:

- you keep a stable soundboard mode in the config
- option `5` in `tyrionTalks` updates the active clip
- a running bridge can switch to the new clip without rewriting `UPLINK_AUDIO_FILE`

---

## Basic test procedure

1. Pair the phone to the Pi.
2. Start the bridge.
3. Make sure the phone stays connected to the Pi as a headset/hands-free device.
4. Place a test call or receive one.
5. Answer the call.
6. Check that:
   - remote voice is heard on the Pi AUX output
   - your voice from the webcam mic reaches the phone call

---

## Troubleshooting

### 1) Phone pairs but call audio still stays on the phone

This is usually **phone policy**, not Pi audio plumbing.

Check on the phone whether the paired device is allowed for:

- Calls / Phone audio / Hands-free

Many phones let you enable or disable call audio per paired device.

### 2) Remote audio works, but the other person cannot hear you

Check webcam mic directly:

```bash
arecord -D plughw:CARD=C920,DEV=0 -f S16_LE -c 1 -r 16000 -d 5 /tmp/test.wav
aplay /tmp/test.wav
```

If that fails, fix ALSA microphone selection first.

### 3) Mic works locally, but uplink still fails in calls

Possible causes:

- wrong `MIC_PCM`
- phone never acquired the SCO transport
- HFP/HSP permissions not granted on the phone
- BlueALSA daemon mismatch or package missing

### 4) `bluealsa` / `bluealsad` command not found

Different distributions expose the daemon under different binary names.

This package auto-detects:

- `bluealsad`
- `bluealsa`

If neither exists, install the BlueALSA packages or build BlueALSA from source.

### 5) Choppy call audio

Try:

- moving the phone closer to the Pi
- disabling Wi‑Fi temporarily for a test (Pi 3 can show 2.4 GHz coexistence issues)
- lowering extra system load
- keeping `BLUEALSA_IO_RT_PRIORITY=20`

### 6) Need to see whether the phone is connected

```bash
bluetoothctl info <PHONE_MAC>
```

### 7) Need to inspect BlueALSA PCMs

```bash
bluealsa-aplay -L
bluealsa-aplay -l
```

### 8) Need to inspect Bluetooth logs

```bash
journalctl -u bluetooth -f
journalctl -u bt-call-bridge.service -f
```

---

## Why this package uses BlueALSA instead of custom chip firmware

Because the practical Linux architecture is:

- Bluetooth controller provides HCI + SCO transport
- BlueZ handles Bluetooth stack / profile registration boundary
- BlueALSA exposes HFP/HSP audio as ALSA-accessible PCMs
- ALSA tools then route audio between physical devices and Bluetooth transports

That means you can solve this cleanly in userspace.

---

## Known limitations / realism check

This package is a **best-effort engineering starting point**, not a guaranteed production telephony appliance.

Important realities:

1. **Phone OS behavior matters.** Some phones are stubborn about when they actually switch the call to the paired hands-free device.
2. **HFP/HSP routing is sensitive to distro versions.** BlueZ/BlueALSA packaging differs across Debian/Raspberry Pi OS releases.
3. **Echo handling is not implemented.** If your AUX speaker is loud and near the webcam mic, the far end may hear echo.
4. **No call signaling UI.** The package only deals with the audio path, not a custom in-call control surface.
5. **No automatic source detection.** You must set the correct ALSA device names in `bridge.conf`.

---

## Recommended next improvements

If you want to evolve this further, the next logical upgrades are:

1. **Echo cancellation / noise suppression**
   - add WebRTC AEC/NS stage or PipeWire/PulseAudio DSP layer
2. **Automatic device discovery**
   - auto-pick USB webcam mic and headphone PCM
3. **Call-state awareness**
   - monitor BlueALSA/BlueZ events and expose clear state transitions
4. **Optional music mix-in**
   - mix a second local PCM source into the microphone uplink
5. **GPIO controls**
   - button for pair/connect, LEDs for connected/in-call state

---

## References

These are the upstream docs that informed the implementation:

- BlueZ Profile API
  - https://bluez.readthedocs.io/en/latest/profile-api/
- BlueALSA project
  - https://github.com/arkq/bluez-alsa
- BlueALSA daemon manual
  - https://github.com/arkq/bluez-alsa/blob/master/doc/bluealsad.8.rst
- BlueALSA ALSA plugin manual
  - https://github.com/arkq/bluez-alsa/blob/master/doc/bluealsa-plugins.7.rst
- bluealsa-aplay manual
  - https://github.com/arkq/bluez-alsa/blob/master/doc/bluealsa-aplay.1.rst
- Debian Bookworm package pages
  - https://packages.debian.org/bookworm/bluez
  - https://packages.debian.org/bookworm/bluez-alsa-utils
  - https://packages.debian.org/bookworm/libasound2-plugin-bluez
  - https://packages.debian.org/bookworm/alsa-utils

---

## One-line summary

This package gives you a **runnable Raspberry Pi Bluetooth call bridge** that routes:

- **phone call audio out to AUX**
- **USB webcam mic audio back into the call**

using **BlueZ + BlueALSA + ALSA**, with a service wrapper and a config file you can edit.
