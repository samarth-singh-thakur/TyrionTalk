# Quickstart for TyrionPiSpeaker

This package has already been aligned to your Pi ALSA devices:

```ini
AUX_PCM=plughw:CARD=Headphones,DEV=0
MIC_PCM=plughw:CARD=C920,DEV=0
```

## 1) Install dependencies

```bash
sudo ./install.sh
```

## 2) Pair the phone

```bash
sudo ./pair_phone.sh ./bridge.conf
```

In another terminal:

```bash
bluetoothctl devices
```

Copy the phone MAC into `PHONE_MAC=` in `bridge.conf`.

If your phone is `Samarth` with MAC `58:43:AB:D9:8C:6E`, set:

```ini
PHONE_MAC=58:43:AB:D9:8C:6E
```

## 3) Start the bridge

```bash
sudo ./start.sh ./bridge.conf
```

## 4) What should happen

- Phone pairs to the Pi as `PiCallBridge` or the alias in your config
- During a call, phone-call audio should play out of the Pi headphone jack
- Your Logitech C920 microphone should feed the call uplink

## 5) Smoke tests

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

## 6) Stop

```bash
sudo ./stop.sh
```
