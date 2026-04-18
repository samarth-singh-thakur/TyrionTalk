# Quickstart for TyrionTalks

This package has already been aligned to your Pi ALSA devices:

```ini
AUX_PCM=plughw:CARD=Headphones,DEV=0
MIC_PCM=plughw:CARD=C920,DEV=0
```

## 1) Install dependencies

```bash
sudo ./install.sh
```

After install, the app is available globally as:

```bash
tyrionTalks
```

The installer also disables conflicting system Bluetooth audio services and forces headset-style `NoInputNoOutput` pairing mode on the Pi.

## 2) Launch the SSH terminal menu

```bash
tyrionTalks
```

From the menu you can:

- choose the phone MAC
- choose the headphone jack output
- choose the uplink source (`mic`, `file`, or `soundboard`)
- pick the C920 mic, a loop file, or the active soundboard clip from the `audio/` library
- optionally mirror the uplink PCM stream to a file, FIFO, or command for future transcription
- pair the phone and start the bridge from the same UI

Put any new `.mp3` files you want to loop into:

```bash
./audio
```

If you installed the package into `/opt/bt-call-bridge`, that path becomes:

```bash
/opt/bt-call-bridge/audio
```

They will then show up in option `5` inside `tyrionTalks`.

If you set `UPLINK_SOURCE=soundboard`, option `5` updates the live soundboard selector file so the running bridge can swap clips without you editing `UPLINK_AUDIO_FILE`.

## 3) What should happen

- Phone pairs to the Pi as `PiCallBridge` or the alias in your config
- During a call, phone-call audio should play out of the Pi headphone jack
- Your Logitech C920 microphone should feed the call uplink

If you prefer the manual flow, you can still run:

```bash
sudo ./pair_phone.sh ./bridge.conf
sudo ./start.sh ./bridge.conf
```

When using `pair_phone.sh`, keep that terminal open while the phone pairs. It runs a temporary Bluetooth call-profile server so the phone sees the correct HFP/HSP capabilities.

If you are running directly from the repo without installing first, use:

```bash
sudo ./terminal.sh ./bridge.conf
```

## 4) Smoke tests

Check devices:

```bash
aplay -l
arecord -l
```

Test Pi headphone jack:

```bash
speaker-test -D plughw:CARD=Headphones,DEV=0 -c 2 -t wav
```

Test webcam mic capture:

```bash
arecord -D plughw:CARD=C920,DEV=0 -f S16_LE -c 1 -r 16000 -d 5 /tmp/test.wav
aplay /tmp/test.wav
```

## 5) Stop

```bash
sudo ./stop.sh
```
