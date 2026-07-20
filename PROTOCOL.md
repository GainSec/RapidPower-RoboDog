# Rapidpower Robot Dog — BLE Protocol

A clean-room description of how the Rapidpower robot dog is controlled over Bluetooth LE,
derived by observing the behaviour of the official `com.zhongrun.robotdog` app. Everything
below is described in our own words from the observed wire format; no proprietary source is
reproduced. It exists so you can interoperate with **your own** device.

---

## 1. Transport & connection

- **Transport:** Bluetooth Low Energy (GATT). The app uses an Android BLE stack; this port
  uses [`bleak`](https://github.com/hbldh/bleak).
- **Advertised name:** `Rapidpower-dog` (a "fire" variant advertises `Rapidpower-dog-fire`).
- **Privacy:** the dog uses a **randomized BLE address** and frequently advertises **no
  local name** in the passive-scan packet, so identifying it by MAC or by name alone is
  unreliable. This port also recognizes it by its custom GATT characteristic UUIDs.
- **Pairing / auth:** **none.** There is no bonding, handshake, or init/enable packet — once
  connected and the notify subscription is set up, the dog accepts commands immediately.

### GATT discovery

The app does not hard-code service/characteristic UUIDs. It walks the GATT table and, **per
service**, selects:

- a **WRITE** characteristic — one whose properties include write or write-without-response
  (property mask `& 0x0C`), and
- a **NOTIFY** characteristic — property mask `& 0x10`.

It accepts the **first service that contains both**. On the observed device these resolve to:

| Role | Characteristic UUID |
|------|---------------------|
| WRITE (commands out) | `b02eaeaa-f6bc-4a7e-bc94-f7b7fc8ded0b` |
| NOTIFY (telemetry in) | `10e2fde2-d7fe-4845-b3f3-a32010ebb095` |

Commands are written to the WRITE characteristic **with response** (the app's default write
type). Writing *without* response is silently ignored by the firmware — this was the single
most important detail for getting the dog to actually move.

---

## 2. Packet format

Every control packet is exactly **7 bytes**:

```
 byte:   0     1        2         3        4      5      6
        F0   TYPE   PARAM_HI  PARAM_LO   CHK0   CHK1   CHK2
       start  cmd   \___ 2-byte param ___/  \__ 3-byte checksum __/
```

- **`F0`** — fixed start byte.
- **`TYPE`** — command class (see §3).
- **`PARAM`** — 2-byte selector within the class.
- **Checksum** — the **ones' complement** of the three body bytes:
  `CHK = (TYPE << 16 | PARAM) XOR 0xFFFFFF`, i.e. each of `TYPE, PARAM_HI, PARAM_LO` XORed
  with `0xFF`.

Example — `forward`: body `2A 00 01` → checksum `D5 FF FE` → packet **`F0 2A 0001 D5FFFE`**.

```python
def frame(type_byte, param):
    body = bytes([type_byte, (param >> 8) & 0xFF, param & 0xFF])
    return bytes([0xF0]) + body + bytes(b ^ 0xFF for b in body)
```

---

## 3. Command classes

The `TYPE` byte selects a class:

| TYPE | Class | Params |
|------|-------|--------|
| `0x2A` | Single actions (move / posture / trick) | opcode per action |
| `0x12` | **Dance** | 4 variants (`0001`–`0004`) |
| `0x18` | **Story** | 4 variants |
| `0x1E` | **Music / songs** | 4 variants |
| `0x24` | **Sleep** | 4 variants |
| `0x30` | Feed items | `0001`–`0008` |
| `0x36` | Per-leg posture | packed nibbles (see §5) |

The four entertainment classes each carry a **random variant** (1 of 4) per press in the
app — a small library of clips. This port defaults to variant 1 and lets you pick a specific
one (`encode('dance', variant=3)`).

### 3.1 Action opcodes (TYPE `0x2A`)

These are taken directly from the app's own command table and are **verified** — the label
is the app's own English action name:

| Opcode | Packet | Action | Port name |
|--------|--------|--------|-----------|
| `0001` | `F02A0001D5FFFE` | move forward | `forward` |
| `0004` | `F02A0004D5FFFB` | turn left | `turn_left` |
| `0007` | `F02A0007D5FFF8` | turn right | `turn_right` |
| `000A` | `F02A000AD5FFF5` | stop | `stop` |
| `000D` | `F02A000DD5FFF2` | swimming | `swim` |
| `0010` | `F02A0010D5FFEF` | sit down | `sit` |
| `0013` | `F02A0013D5FFEC` | greetings / hello | `greet` |
| `0016` | `F02A0016D5FFE9` | get down | `get_down` |
| `0019` | `F02A0019D5FFE6` | act cute | `act_cute` |
| `001C` | `F02A001CD5FFE3` | handshake | `shake_hand` |
| `001F` | `F02A001FD5FFE0` | attack | `attack` |
| `0022` | `F02A0022D5FFDD` | surrender | `surrender` |
| `0025` | `F02A0025D5FFDA` | urinate | `pee` |
| `0028` | `F02A0028D5FFD7` | stand / hand-stand | `handstand` |
| `002B` | `F02A002BD5FFD4` | patrol | `patrol` |
| `002E` | `F02A002ED5FFD1` | kung fu | `kung_fu` |
| `0031` | `F02A0031D5FFCE` | push-up | `push_up` |

### 3.2 Inferred movement opcodes

The action table advances in a regular +3 stride (`0001, 0004, 0007, …`), leaving 2-wide
gaps between the confirmed opcodes. The dashboard's D-pad needs `backward` and lateral
strafe, which map naturally into those gaps but were **not** observed as explicit literals:

| Opcode | Port name | Status |
|--------|-----------|--------|
| `0002` | `backward` | inferred |
| `0005` | `left_shift` | inferred |
| `0008` | `right_shift` | inferred |
| `0011` | `stand_up` | inferred |

They are included for convenience but flagged as unverified until confirmed on hardware.

### 3.3 Entertainment (TYPE `0x12/0x18/0x1E/0x24`)

`dance`, `story`, `music`, `sleep` — each variant `0001`–`0004`, e.g.:

```
dance v1  F0120001EDFFFE     story v1  F0180001E7FFFE
music v1  F01E0001E1FFFE     sleep v1  F0240001DBFFFE
```

### 3.4 Feed items (TYPE `0x30`)

`feed_water=0001, feed_bone=0002, feed_ice=0003, feed_gem=0004, feed_battery=0005,`
`feed_sun=0006, feed_oil=0007, feed_nucleus=0008`, e.g. `feed_bone` → `F0300002CFFFFD`.

---

## 4. Telemetry (NOTIFY)

The dog emits notifications on the NOTIFY characteristic once you subscribe. The app logs
these but does not act on a documented decoded structure, so this port surfaces them **raw**
(hex) in the dashboard log and over `/ws/telemetry`. Decoding the telemetry fields (battery,
pose, sensors) is left as future work and would need capture from a known device state.

---

## 5. Per-leg posture (TYPE `0x36`)

Each leg angle maps to a small integer code (0–6); the four codes for
front-left / front-right / back-left / back-right are packed as hex nibbles after the `0x36`
type byte, then checksummed the same way as every other packet.

| Angle (°) | Front code | Back code |
|-----------|-----------|-----------|
| 0 | 4 | 3 |
| ±40 | 5 | — |
| ±45 | 3 | 4 |
| ±50 | — | 2 |
| ±75 | — | 5 |
| ±80 | 6 | — |
| ±85 | 2 | — |
| ±90 | — | 1 |
| ±110 | — | 6 |
| 120 | 1 | — |

Neutral pose `(0,0,0,0)` → body `36 4 4 3 3` = `364433` → `364433 XOR FFFFFF = C9BBCC` →
packet **`F0364433C9BBCC`**. The absolute value of the angle is used, so `+45` and `−45`
share a code.

---

## 6. Practical notes

- **Only one BLE connection at a time.** Fully close the phone app before connecting from
  this port, or the dog stays bonded to the phone and won't respond (or won't advertise).
- **Write *with* response.** See §1 — this is the difference between "commands sent, nothing
  moves" and a working dog.
- **On Linux**, BlueZ must be running (`systemctl enable --now bluetooth`) and the adapter
  powered (`bluetoothctl power on`) even though the radio reports "UP" at the HCI level.
- **Active scanning** (requesting scan responses) improves the odds of seeing the dog's
  name; passive scanning often shows it nameless.

---

<sub>Reverse-engineered & documented by **Jon "GainSec" Gaines** · [gainsec.com](https://gainsec.com)</sub>
