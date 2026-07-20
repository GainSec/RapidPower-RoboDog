# 🐕‍🦺 RoboDog — Python Control for the Rapidpower Robot Dog

A clean, original Python port that lets you control a **Rapidpower robot dog** over
Bluetooth LE directly from your computer — no phone app required — via a simple Python
API and a polished **web dashboard**.

It was reconstructed, clean-room, by observing the wire protocol of the official
`com.zhongrun.robotdog` Android app so you can control **your own device** locally
(interoperability). No proprietary source is reproduced here.

![status](https://img.shields.io/badge/status-working%20on%20real%20hardware-brightgreen)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![ble](https://img.shields.io/badge/transport-Bluetooth%20LE%20(bleak)-5cc8ff)

[Overview Page](https://gainsec.github.io/RapidPower-RoboDog/)
---

## 🛒 The hardware

This controls the **Rapidpower Smart Dog** (Android app package `com.zhongrun.robotdog`) —
a Bluetooth/voice/app robot dog toy. It's sold under generic "smart robot dog" listings:

- **AliExpress:** <https://www.aliexpress.com/w/wholesale-rapidpower-robot-dog.html>
- Also found on eBay ("Rapidpower Smart Dog") and via the
  [Rapidpower app on Google Play](https://play.google.com/store/apps/details?id=com.zhongrun.robotdog).

You need a unit that pairs with the *Rapidpower* app (advertises as `Rapidpower-dog`).

## ✨ Features

- **Web dashboard** — scan, connect, and drive the dog from any browser on your network.
- **Full command set** — movement, postures, tricks, entertainment (dance/story/music/sleep),
  per-leg posing, and feed items.
- **Live telemetry** — the dog's notifications stream to the dashboard over a WebSocket.
- **Cross-platform BLE** via [`bleak`](https://github.com/hbldh/bleak) (Linux/BlueZ, macOS, Windows).
- **Plain Python API** — script the dog in a few lines.
- **No auth, no pairing** — the dog accepts commands as soon as you connect.

---

## 🚀 Quick start

```bash
git clone https://github.com/GainSec/RoboDog.git
cd RoboDog
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt

# start the dashboard server
python -m robodog.server --host 0.0.0.0 --port 8770
```

Then open **http://localhost:8770/** (or `http://<host-ip>:8770/` from another device on
the LAN). Power the dog on, close the phone app, then **Scan → Connect → drive**.

> **Bluetooth must run on the machine with the BLE radio.** If you run the server on a
> Linux box, make sure BlueZ is up: `sudo systemctl enable --now bluetooth`.

---

## 🖥️ The dashboard

| Panel | What it does |
|-------|--------------|
| **Connection** | `Scan` (dog only) / `Scan all` (every BLE device) → pick device → `Connect` |
| **Movement** | D-pad: forward / back / turn / strafe / stop |
| **Postures & Tricks** | sit, stand, greet, shake, kung-fu, handstand, surrender, … |
| **Entertainment** | dance, story, music, sleep |
| **Leg Control** | pose each leg individually with sliders |
| **Feed** | the 8 in-app "feed" items |
| **Telemetry & Log** | live packet log + inbound notifications |

### Finding the dog

The dog uses **BLE privacy** — its MAC address randomizes and it often advertises no
name. The dashboard identifies it two ways:

1. by advertised name (`Rapidpower-dog`) when available, and
2. by its **known GATT characteristic UUIDs** (surfaced via `Scan all`).

If `Scan` shows nothing, hit **Scan all** and connect to the strongest-signal nameless
device — that's almost always the dog. Make sure the **phone app is fully closed** first,
since BLE only allows one connection at a time.

---

## 🐍 Python API

```python
import asyncio
from robodog import RobodogBLE

async def demo():
    dog = RobodogBLE()
    await dog.connect()                 # auto-scan + connect to the first dog found
    await dog.send_command('sit')
    await asyncio.sleep(2)
    await dog.send_command('dance')     # entertainment: plays a dance clip
    await dog.send_command('forward')
    await asyncio.sleep(1)
    await dog.send_command('stop')
    await dog.send_feed('feed_bone')
    await dog.send_legs(fl=0, fr=0, bl=0, br=0)   # neutral leg pose
    await dog.disconnect()

asyncio.run(demo())
```

Encode packets without a device:

```python
from robodog.protocol import encode
encode('forward').hex().upper()     # 'F02A0001D5FFFE'
encode('surrender').hex().upper()   # 'F02A0022D5FFDD'
encode('dance', variant=3).hex()    # a specific dance clip
```

---

## 🎮 Command reference

**Movement:** `forward`, `backward`*, `turn_left`, `turn_right`, `left_shift`*,
`right_shift`*, `stop`, `swim`

**Postures & tricks:** `sit`, `stand_up`*, `greet`, `get_down`, `act_cute`,
`shake_hand`, `attack`, `surrender`, `pee`, `handstand`, `patrol`, `kung_fu`, `push_up`

**Entertainment (4 variants each):** `dance`, `story`, `music`, `sleep`

**Feed:** `feed_water`, `feed_bone`, `feed_ice`, `feed_gem`, `feed_battery`, `feed_sun`,
`feed_oil`, `feed_nucleus`

**Legs:** `send_legs(fl, fr, bl, br)` — front angles `0, ±40, ±45, ±80, ±85, 120`;
back angles `0, ±45, ±50, ±75, ±90, ±110`.

\* *Movement opcodes marked with an asterisk are inferred from the command table's
stride and not yet independently confirmed on hardware — see [PROTOCOL.md](PROTOCOL.md).*

Older names (`squat`, `wag_tail`, `lie_down`, `swing`, `stretch`, `come_here`, `prone`)
still work as **aliases** for the corrected names above.

---

## 🌐 HTTP / WebSocket API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/scan?all=<bool>` | scan for the dog (or all BLE devices) |
| `POST` | `/api/connect` | connect (`{"address": "..."}` optional; auto-scans if omitted) |
| `POST` | `/api/disconnect` | disconnect |
| `GET`  | `/api/status` | connection status |
| `POST` | `/api/command/{name}` | send a movement/posture/trick/entertainment command |
| `POST` | `/api/feed/{name}` | send a feed item |
| `POST` | `/api/legs` | `{fl, fr, bl, br}` leg pose |
| `GET`  | `/api/commands` | list every valid command name |
| `WS`   | `/ws/telemetry` | live telemetry stream |

---

## 📡 Protocol summary

Every packet is **7 bytes**: `F0 | TYPE | PARAM(2) | CHECKSUM(3)`, where the checksum is
the ones' complement (`XOR 0xFFFFFF`) of `TYPE+PARAM`. Command classes are selected by the
`TYPE` byte (`0x2A` actions, `0x12/0x18/0x1E/0x24` entertainment, `0x30` feed, `0x36` legs).
No pairing or auth is required. Full details, including the checksum derivation, GATT
discovery, and the complete opcode table, are in **[PROTOCOL.md](PROTOCOL.md)**.

---

## 🧪 Verify the encoder

```bash
python robodog/protocol.py     # self-test: every packet checked against known-good values
```

---

## 🧭 Project layout

```
robodog/
  protocol.py       # packet encoder (pure, dependency-free) + self-test
  controller.py     # bleak BLE controller: scan / connect / write / notify
  server.py         # FastAPI server: REST + WebSocket + serves the dashboard
  static/
    dashboard.html  # single-file web dashboard
requirements.txt
docs/index.html     # project overview (GitHub Pages)
PROTOCOL.md         # reverse-engineered protocol write-up
```

---

## ⚖️ Legal & clean-room note

This is an **original implementation** written from scratch. It documents an interface
(byte layouts, UUIDs, command flow) for the purpose of **interoperability with your own
hardware**. No decompiled or proprietary source code is included or redistributed.
"Rapidpower" and `com.zhongrun.robotdog` are the property of their respective owners; this
project is not affiliated with or endorsed by them. Use it on devices you own. See
[LICENSE](LICENSE).

---

<sub>Made by **Jon "GainSec" Gaines** · [gainsec.com](https://gainsec.com)</sub>
