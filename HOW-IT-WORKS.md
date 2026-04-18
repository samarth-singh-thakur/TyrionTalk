# Bluetooth Call Bridge: Theory, Profiles, and Practical Operation

This document explains how the package works internally, which Bluetooth profiles it relies on, what each script does, what gets installed, what to run, what to stop, and how to use it in practice.

It is written against the code in this repository, especially:

- `install.sh`
- `pair_phone.sh`
- `start.sh`
- `stop.sh`
- `scripts/bt_call_bridge.py`
- `systemd/bt-call-bridge.service`

## 1. What this project is trying to do

The package turns a Raspberry Pi into a Bluetooth call endpoint for a phone:

- phone call audio from the phone goes to the Pi over Bluetooth and plays on the Pi AUX/headphone output
- microphone audio from a USB webcam or USB mic connected to the Pi goes back to the phone over Bluetooth
- optionally, a local audio file can be looped into the phone call instead of using the live mic

In short:

```text
Phone <-> Bluetooth call profile <-> Raspberry Pi <-> AUX speaker + USB microphone
```

This is not custom Bluetooth firmware. It is a Linux userspace bridge built from:

- BlueZ for Bluetooth control and pairing
- BlueALSA for exposing Bluetooth audio as ALSA devices
- ALSA tools like `arecord`, `aplay`, and `bluealsa-aplay` for moving audio around
- a Python supervisor that keeps the bridge processes alive

## 2. End-to-end architecture

The runtime data path looks like this:

```text
Downlink path
Phone call audio
-> HFP/HSP over Bluetooth
-> SCO audio transport
-> BlueALSA
-> bluealsa-aplay
-> ALSA playback device
-> Pi headphone jack / AUX speaker

Uplink path
USB webcam microphone
-> ALSA capture device
-> arecord
-> aplay to BlueALSA SCO PCM
-> BlueALSA
-> SCO audio transport
-> HFP/HSP over Bluetooth
-> Phone

Alternate uplink path
Local audio file
-> ffmpeg decode + loop
-> aplay to BlueALSA SCO PCM
-> BlueALSA
-> SCO audio transport
-> HFP/HSP over Bluetooth
-> Phone
```

The control path looks like this:

```text
pair_phone.sh / bt_call_bridge.py
-> bluetoothctl
-> power on adapter
-> set alias
-> make adapter pairable/discoverable
-> trust/connect known phone
```

## 3. The Bluetooth profiles involved

This project depends on Bluetooth call profiles, not just generic Bluetooth pairing.

### HFP: Hands-Free Profile

HFP is the main profile used for phone calls in modern Bluetooth hands-free devices.

- It supports bidirectional call audio
- It normally runs over SCO/eSCO audio transport
- It can support narrowband speech and, depending on the stack and phone, wideband speech

In this package, the Pi starts BlueALSA with:

```text
--profile hfp-hf
```

That means the Pi presents itself as the Hands-Free side, while the phone behaves as the Audio Gateway side.

The saved phone info in the snapshot shows the phone advertising:

- `Handsfree Audio Gateway`

which matches this design.

### HSP: Headset Profile

HSP is the older, simpler call-audio profile.

- It also carries bidirectional voice audio
- It is less capable than HFP
- It is still useful as a fallback path on some devices

In this package, the Pi also starts BlueALSA with:

```text
--profile hsp-hs
```

That means the Pi can also behave as a Headset side device, while the phone behaves as the Headset Audio Gateway.

The saved phone info shows the phone advertising:

- `Headset AG`

which again matches the intended direction of the link.

### SCO / eSCO

SCO is the low-latency synchronous Bluetooth audio transport used underneath HFP/HSP voice audio.

This project is really about routing SCO audio correctly:

- downlink uses `bluealsa-aplay --profile-sco`
- uplink uses `aplay -D bluealsa:DEV=<PHONE_MAC>,PROFILE=sco`

So when the phone actually routes the call to the Pi, the bridge is feeding and consuming the SCO path.

### A2DP Sink

The code can optionally enable:

```text
--profile a2dp-sink
```

through:

```ini
ENABLE_A2DP_SINK=1
```

But that is optional and separate from the main call bridge.

Important distinction:

- HFP/HSP are for two-way call audio
- A2DP is mainly for one-way higher-quality media playback

So A2DP is not the core of this project. The actual call bridge depends on HFP/HSP plus SCO.

## 4. Why this project uses BlueZ + BlueALSA + ALSA

The project uses the standard Linux layering for Bluetooth call audio:

1. The Pi Bluetooth controller handles the radio and HCI/SCO transport.
2. BlueZ handles pairing, trust, connect, and adapter state.
3. BlueALSA exposes Bluetooth audio endpoints to ALSA.
4. ALSA tools move audio between local hardware devices and the Bluetooth endpoints.

This is the practical Linux solution because it avoids modifying firmware and keeps the logic visible in userspace.

## 5. What the installer does

Running:

```bash
sudo ./install.sh
```

does all of the following:

1. Verifies it is being run as root.
2. Installs required packages with `apt-get`:
   - `bluez`
   - `bluez-alsa-utils`
   - `libasound2-plugin-bluez`
   - `alsa-utils`
   - `ffmpeg`
   - `python3`
3. Creates the install target directory, defaulting to:
   - `/opt/bt-call-bridge`
4. Deletes existing contents of `/opt/bt-call-bridge` except an already-existing `bridge.conf`.
5. Copies this repository into `/opt/bt-call-bridge`.
6. If `/opt/bt-call-bridge/bridge.conf` does not exist, copies `bridge.conf.example` to create it.
7. Installs a global launcher to:
   - `/usr/local/bin/tyrionTalks`
8. Creates and populates the audio library directory:
   - `/opt/bt-call-bridge/audio`
9. Installs the systemd service file to:
   - `/etc/systemd/system/bt-call-bridge.service`
10. Stops and disables conflicting system Bluetooth audio services:
   - `bluealsa.service`
   - `bluealsa-aplay.service`
   - `bt-speaker-agent.service`
   - `ofono.service`
11. Forces headset-style pairing mode on the controller with:
   - `btmgmt io-cap 0x03`
12. Runs:
   - `systemctl daemon-reload`

What `install.sh` does not do:

- it does not pair the phone
- it does not enable the service automatically
- it does not start the service automatically
- it does not stop PipeWire or other user audio services

After install, the intended next steps are:

1. run `tyrionTalks` from anywhere in the terminal
2. add any new loop audio files to the package's `audio/` directory
   after install this is usually `/opt/bt-call-bridge/audio`
3. save or adjust the config from the menu
4. pair the phone and start the bridge from the menu, or enable the service

## 6. What each script does

### `pair_phone.sh`

Typical use:

```bash
sudo ./pair_phone.sh ./bridge.conf
```

What it does:

1. Loads `bridge.conf`.
2. Starts a temporary BlueALSA server with the HFP/HSP call profiles enabled.
3. Stops conflicting bridge, Bluetooth audio, and user audio services that can interfere with pairing.
4. Forces controller IO capability with `btmgmt io-cap 0x03`.
5. Runs a persistent `bluetoothctl` session and forces:
   - powered on
   - `agent NoInputNoOutput`
   - `default-agent`
   - pairable on
   - discoverable on
   - discoverable-timeout 0
6. Sets the adapter alias shown on the phone.
7. Keeps the temporary agent and call-profile server alive until pairing finishes.

Important practical note:

- `pair_phone.sh` always makes the adapter pairable and discoverable with timeout `0`
- it uses `BT_ALIAS`, `BT_HCI`, `BT_AGENT_CAPABILITY`, and `BT_IO_CAPABILITY`
- it does not read the config flags `PAIRABLE`, `DISCOVERABLE`, `PAIRABLE_TIMEOUT`, or `DISCOVERABLE_TIMEOUT`
- those configurable flags are used by the Python bridge after pairing, not by `pair_phone.sh`

Its purpose is simple: make the Pi visible to the phone so you can complete initial pairing and then copy the phone MAC address into `PHONE_MAC`.

### `start.sh`

Typical use:

```bash
sudo ./start.sh ./bridge.conf
```

What it does:

1. Resolves the config path.
2. Verifies the config file exists.
3. Executes:

```bash
python3 scripts/bt_call_bridge.py --config <config-path>
```

It is only a thin wrapper around the Python bridge.

### `stop.sh`

Typical use:

```bash
sudo ./stop.sh
```

or, if you are running outside `/opt`:

```bash
sudo ./stop.sh ./bridge.conf
```

What it does:

1. Best-effort loads the config file.
2. Stops `bt-call-bridge.service` if it exists.
3. Kills bridge-related processes with `pkill`.
4. Force-kills any matching leftover processes.
5. Starts some standard system services again:
   - `bluealsa.service`
   - `bluealsa-aplay.service`
   - `bt-speaker-agent.service`
6. Tries to restore the normal user audio session:
   - `pipewire.socket`
   - `pipewire-pulse.socket`
   - `wireplumber.service`
   - `pipewire.service`
   - `pipewire-pulse.service`

Important practical note:

- `stop.sh` is designed as a cleanup and recovery helper
- it assumes a fairly specific deployment shape
- some of its `pkill` patterns are hard-coded for `hci0`, the C920 mic, and `/opt/bt-call-bridge`

So it works best when the package is installed in the default location and still uses the expected device naming.

### `scripts/bt_call_bridge.py`

This is the main runtime logic.

It does five major jobs:

1. parses the key-value config file
2. configures the Bluetooth adapter through `bluetoothctl`
3. starts the BlueALSA daemon with the required profiles
4. starts the downlink and uplink audio workers
5. supervises those child processes and restarts them if they exit

The uplink worker can now run in two modes:

- live microphone capture from `MIC_PCM`
- looped file playback from `UPLINK_AUDIO_FILE`

Its subcommands are also useful:

```bash
python3 scripts/bt_call_bridge.py --list-alsa
python3 scripts/bt_call_bridge.py --config ./bridge.conf --preflight
```

### `systemd/bt-call-bridge.service`

This is the system service wrapper.

It runs:

```text
ExecStart=/opt/bt-call-bridge/start.sh /opt/bt-call-bridge/bridge.conf
```

and sets:

- `Restart=always`
- `RestartSec=3`

So when enabled, systemd keeps the bridge running at boot and restarts the top-level process if it exits.

## 7. What the Python bridge does at runtime

When `bt_call_bridge.py` starts, it performs the following sequence.

### Step 1: load configuration

It reads `bridge.conf` as simple `KEY=VALUE` pairs and falls back to built-in defaults when values are missing.

Important settings include:

- `PHONE_MAC`
- `BT_ALIAS`
- `BT_HCI`
- `BT_AGENT_CAPABILITY`
- `BT_IO_CAPABILITY`
- `AUX_PCM`
- `MIC_PCM`
- `UPLINK_SOURCE`
- `UPLINK_AUDIO_FILE`
- `SOUNDBOARD_SELECTOR_PATH`
- `SCO_RATE`
- `AUTO_CONNECT`
- `DISCOVERABLE`
- `PAIRABLE`
- `PAIRABLE_TIMEOUT`
- `DISCOVERABLE_TIMEOUT`
- `ENABLE_A2DP_SINK`

### Step 2: locate required binaries

It checks that these commands exist:

- `bluealsad` or `bluealsa`
- `bluealsa-aplay`
- `bluetoothctl`
- `aplay`

Then, depending on uplink mode, it also requires:

- `arecord` for `UPLINK_SOURCE=mic`
- `ffmpeg` for `UPLINK_SOURCE=file` or `UPLINK_SOURCE=soundboard`

If one is missing, startup fails early with a clear error.

### Step 3: configure the adapter

Before talking to `bluetoothctl`, the bridge forces controller pairing mode with `btmgmt`:

- `power off`
- `io-cap <BT_IO_CAPABILITY>`
- `bondable on`
- `connectable on`
- `ssp on`
- `power on`

It sends commands to `bluetoothctl` to:

- power on the adapter
- force `agent <BT_AGENT_CAPABILITY>`
- set the adapter alias
- make the adapter pairable if configured
- make the adapter discoverable if configured
- apply the configured timeouts

Unlike `pair_phone.sh`, this part does obey the config booleans and timeout values.

### Step 4: trust and connect the phone

If `PHONE_MAC` is set:

- it sends `trust <PHONE_MAC>`
- if `AUTO_CONNECT=1`, it also sends `connect <PHONE_MAC>`

If `PHONE_MAC` is blank, it skips this step.

### Step 5: start BlueALSA

The script launches the BlueALSA daemon with options built from the config.

The core profiles are:

- `hfp-hf`
- `hsp-hs`

Optionally:

- `a2dp-sink`

It also applies:

- initial volume
- keep-alive policy
- real-time I/O priority
- selected Bluetooth adapter like `hci0`

### Step 6: start the downlink worker

The downlink command is:

```bash
bluealsa-aplay --profile-sco --pcm="$AUX_PCM" "$PHONE_MAC"
```

Its job is:

- listen for the phone's SCO call audio
- play that audio into the configured ALSA output device

In this repo's default setup, that output is usually:

```ini
AUX_PCM=plughw:CARD=Headphones,DEV=0
```

which maps to the Pi headphone jack.

### Step 7: start the uplink worker

If `UPLINK_SOURCE=mic`, the uplink worker is a shell pipeline:

```bash
arecord -D "$MIC_PCM" -q -f S16_LE -c 1 -r "$SCO_RATE" \
  | aplay -D "bluealsa:DEV=$PHONE_MAC,PROFILE=sco" -q -f S16_LE -c 1 -r "$SCO_RATE"
```

Its job is:

- capture mono 16-bit audio from the configured mic
- send it into the Bluetooth SCO PCM exposed by BlueALSA

In this repo's current default setup, the mic is:

```ini
MIC_PCM=plughw:CARD=C920,DEV=0
```

which matches the saved Logitech C920 webcam microphone.

If `UPLINK_SOURCE=file`, the bridge starts this looped file pipeline instead:

```bash
ffmpeg -hide_banner -loglevel error -nostdin -stream_loop -1 -re -i "$UPLINK_AUDIO_FILE" -vn -f s16le -acodec pcm_s16le -ac 1 -ar "$SCO_RATE" - \
  | aplay -D "bluealsa:DEV=$PHONE_MAC,PROFILE=sco" -q -f S16_LE -c 1 -r "$SCO_RATE"
```

Its job is:

- open the configured local audio file
- decode it continuously
- loop it forever
- convert it to mono 16-bit PCM at the SCO rate
- feed that audio back to the caller instead of using live mic capture

If `UPLINK_SOURCE=soundboard`, the bridge starts a small helper process instead of a fixed `ffmpeg -i <file>` pipeline.

That helper:

- watches `SOUNDBOARD_SELECTOR_PATH`
- uses `UPLINK_AUDIO_FILE` as the fallback/default clip
- restarts the decoder whenever the selected clip changes
- keeps the main SCO uplink pipeline alive while clips are swapped

### Step 8: supervise the workers

The script keeps a dictionary of child processes:

- `bluealsa`
- `downlink`
- `uplink`

Every 5 seconds it checks whether each one is still alive.

If one exits:

- it logs the exit
- waits `RESTART_DELAY_SECS`
- restarts that worker

If `AUTO_CONNECT=1`, it also re-runs the trust/connect step in this loop.

### Step 9: shut down cleanly on signals

On `SIGINT` or `SIGTERM`, it:

- logs the signal
- terminates all child processes
- waits briefly
- kills any child that does not exit cleanly

## 8. Config file meaning

The bridge is controlled by `bridge.conf`.

A typical file in this repo looks like:

```ini
PHONE_MAC=58:43:AB:D9:8C:6E
BT_ALIAS=PiCallBridge
BT_HCI=hci0
AUX_PCM=plughw:CARD=Headphones,DEV=0
MIC_PCM=plughw:CARD=C920,DEV=0
UPLINK_SOURCE=file
UPLINK_AUDIO_FILE=KBC_PRANK.mp3
SOUNDBOARD_SELECTOR_PATH=.soundboard-current.txt
SCO_RATE=16000
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

What the most important keys mean:

- `PHONE_MAC`: the phone to trust/connect and the Bluetooth device used for the audio bridge
- `BT_ALIAS`: the name shown on the phone during pairing
- `BT_HCI`: the Bluetooth adapter, usually `hci0`
- `AUX_PCM`: the ALSA playback target for the remote caller's voice
- `MIC_PCM`: the ALSA capture source for your local microphone
- `UPLINK_SOURCE`: `mic` for live mic capture, `file` for looped audio-file playback, or `soundboard` for hot-swappable clip playback
- `UPLINK_AUDIO_FILE`: local file to decode and loop when `UPLINK_SOURCE=file`, and the fallback/default clip for `soundboard`
- `SOUNDBOARD_SELECTOR_PATH`: text file that stores the currently selected soundboard clip
- `SCO_RATE`: usually `16000`, which is a sensible default for wideband-capable HFP audio
- `AUTO_CONNECT`: whether the bridge should keep trying to connect the configured phone
- `DISCOVERABLE` and `PAIRABLE`: whether the adapter stays visible/pairable while the bridge runs
- `ENABLE_A2DP_SINK`: optional media sink support, not required for voice calls

## 9. What to run in practice

### First-time setup

1. Install packages and service files:

```bash
sudo ./install.sh
```

2. List ALSA devices:

```bash
aplay -l
arecord -l
python3 scripts/bt_call_bridge.py --list-alsa
```

3. Edit the config:

```bash
nano bridge.conf
```

4. Make the Pi visible and pair from the phone:

```bash
sudo ./pair_phone.sh ./bridge.conf
```

5. On the Pi, find the phone MAC:

```bash
bluetoothctl devices
```

6. Put that MAC into `PHONE_MAC=...` in `bridge.conf`.

7. Run a preflight check:

```bash
python3 scripts/bt_call_bridge.py --config ./bridge.conf --preflight
```

8. Start the bridge:

```bash
sudo ./start.sh ./bridge.conf
```

To have the caller hear the bundled prank audio on loop, set:

```ini
UPLINK_SOURCE=file
UPLINK_AUDIO_FILE=KBC_PRANK.mp3
```

and then start the bridge normally.

### Service mode

If installed into `/opt/bt-call-bridge`, use:

```bash
sudo systemctl enable --now bt-call-bridge.service
sudo systemctl status bt-call-bridge.service
journalctl -u bt-call-bridge.service -f
```

### Manual stop

To stop the bridge and restore normal audio services as much as possible:

```bash
sudo ./stop.sh
```

If you are working from the repo instead of `/opt`, use:

```bash
sudo ./stop.sh ./bridge.conf
```

### If there is an audio-stack conflict

The snapshot restore notes show that a working setup may sometimes need the desktop user audio stack stopped manually before testing:

```bash
systemctl --user stop pipewire pipewire-pulse wireplumber
```

This is not done automatically by `start.sh` or `bt_call_bridge.py`, so it is a manual troubleshooting step when the normal desktop audio session interferes with device access.

## 10. What to expect during an actual call

When things are working correctly:

1. The phone pairs to the Pi as `PiCallBridge` or your configured alias.
2. The phone connects to the Pi as a hands-free/headset audio device.
3. A phone call starts or is answered.
4. The phone routes call audio to the Bluetooth call profile.
5. The remote party's voice plays from the Pi AUX/headphone output.
6. Your USB webcam mic audio is sent back to the phone call.

If `UPLINK_SOURCE=file`, step 6 changes to:

- the caller hears the configured local audio file on loop instead of live mic audio

If `UPLINK_SOURCE=soundboard`, step 6 changes to:

- the caller hears whichever clip is currently selected in the soundboard selector file
- switching clips from the terminal UI updates that selector without needing to rewrite the main file setting

What this project does not do:

- it does not answer the call for you
- it does not force the phone OS to use the Pi for calls if the phone refuses
- it does not provide echo cancellation
- it does not provide an in-call GUI
- it does not mix music or extra local audio into the uplink

## 11. Theory: why phone behavior still matters

Even if the Pi side is configured correctly, the phone still decides whether to route the call to the paired Bluetooth device.

That means:

- pairing success alone is not enough
- connection success alone is not enough
- the phone must also activate HFP/HSP call audio for that device

Many phones expose this as a toggle like:

- Calls
- Phone audio
- Hands-free

If the phone keeps the call on its own speaker/earpiece, the bridge may be running correctly but never receive an active SCO stream.

## 12. Theory: why `SCO_RATE=16000` is used

The project defaults to:

```ini
SCO_RATE=16000
```

Reason:

- 16 kHz is a good default for wideband-capable HFP audio
- if the actual link falls back to a narrower mode, BlueALSA and the stack can still adapt the transport

The important thing is not perfect studio fidelity. The goal is a stable mono voice path for phone calls.

## 13. Practical device mapping in this repo

The saved working snapshot shows:

- playback device:
  - `card 1: Headphones`, device `0`
- capture device:
  - `card 2: C920`, device `0`
- paired phone:
  - `58:43:AB:D9:8C:6E`

That maps to:

```ini
AUX_PCM=plughw:CARD=Headphones,DEV=0
MIC_PCM=plughw:CARD=C920,DEV=0
PHONE_MAC=58:43:AB:D9:8C:6E
```

The saved Bluetooth info also shows the phone advertising both:

- `Headset AG`
- `Handsfree Audio Gateway`

which is exactly what the Pi-side `hsp-hs` and `hfp-hf` configuration expects.

The saved BlueALSA state also shows endpoints for:

- `hsphs`
- `hfphf`
- `a2dpsnk`

which confirms that the system has seen both call-audio and media-audio profile state.

## 14. Troubleshooting checklist

### Pairing works, but calls do not route

Check on the phone that Bluetooth call audio is enabled for the Pi device.

### Remote audio works, but the far end cannot hear you

Test the mic locally:

```bash
arecord -D plughw:CARD=C920,DEV=0 -f S16_LE -c 1 -r 16000 -d 5 /tmp/test.wav
aplay /tmp/test.wav
```

If that fails, the issue is local mic capture, not Bluetooth uplink.

### The bridge starts, but reconnects poorly

Inspect:

```bash
bluetoothctl info <PHONE_MAC>
journalctl -u bt-call-bridge.service -f
journalctl -u bluetooth -f
```

### Standard desktop audio behaves strangely after testing

Stop the bridge and restore normal services:

```bash
sudo ./stop.sh
```

If needed, also verify:

```bash
systemctl --user status pipewire wireplumber pipewire-pulse
systemctl status bluealsa bluealsa-aplay bt-speaker-agent
```

### You changed devices or install location

Be aware that `stop.sh` contains some hard-coded assumptions and may need adjustment if:

- the mic is no longer `plughw:CARD=C920,DEV=0`
- the adapter is not `hci0`
- the package is not installed in `/opt/bt-call-bridge`

## 15. Snapshot and restore utilities

The `snapshots/` area is not part of the live audio bridge, but it is useful operationally.

### `snapshots/snapshot_bt_bridge.sh`

This helper creates a restorable snapshot of a working system. It captures:

- `/opt/bt-call-bridge`
- `/etc/systemd/system/bt-call-bridge.service`
- `/var/lib/bluetooth`
- `/var/lib/bluealsa`
- package inventory
- Bluetooth diagnostics
- ALSA diagnostics
- system and user audio service state

This is useful because Bluetooth pairing state and BlueALSA state can matter just as much as the application files.

### `restore_snapshot.sh`

The generated restore script can:

- stop the bridge service
- restore the app files
- restore the systemd unit
- restore Bluetooth and BlueALSA state
- reload systemd
- re-enable the bridge service

The restore notes also remind you that if normal desktop audio services conflict again, you may need:

```bash
systemctl --user stop pipewire pipewire-pulse wireplumber
```

So the snapshot tooling is best understood as an operational recovery layer around the main bridge.

## 16. Summary

This package works by combining:

- BlueZ for Bluetooth control
- BlueALSA for Bluetooth audio endpoints
- ALSA tools for capture/playback plumbing
- a Python supervisor that continually keeps the bridge alive

The important Bluetooth profiles are:

- `hfp-hf`
- `hsp-hs`
- underlying `sco`

The most important operational commands are:

```bash
sudo ./install.sh
sudo ./pair_phone.sh ./bridge.conf
python3 scripts/bt_call_bridge.py --config ./bridge.conf --preflight
sudo ./start.sh ./bridge.conf
sudo ./stop.sh
```

If the phone agrees to route call audio over HFP/HSP, the Pi becomes a practical bridge:

- remote caller voice comes out of the Pi AUX output
- local USB microphone audio goes back into the phone call
