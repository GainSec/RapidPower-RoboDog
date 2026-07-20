r"""
Rapidpower robot dog BLE protocol encoder.

Original, clean-room implementation reconstructed from observing the
com.zhongrun.robotdog Android app. No proprietary source is reproduced here —
this module re-derives the wire format from the observed packet structure.

Wire format (all packets are 7 bytes):

    F0 | TYPE | PARAM_HI | PARAM_LO | CHK0 | CHK1 | CHK2
    \__/  \__/  \_______________/     \________________/
   start  cmd      2-byte param        3-byte checksum

  * TYPE selects the command class (see below).
  * PARAM is a 2-byte selector within that class.
  * The 3-byte checksum is the ones' complement (XOR 0xFF) of TYPE+PARAM_HI+PARAM_LO,
    i.e. checksum = (TYPE PARAM_HI PARAM_LO) XOR 0xFFFFFF.

Command classes (TYPE byte):
    0x2A  single actions (move / posture / trick)      -> ACTIONS
    0x12  dance     (4 variants)  \
    0x18  story     (4 variants)   |  entertainment / audio, one of N variants
    0x1E  music     (4 variants)   |  is chosen per press
    0x24  sleep     (4 variants)  /
    0x30  feed items (8 items)                          -> FEED_COMMANDS
    0x36  per-leg posture (dynamic nibble encoding)     -> encode_legs()
"""

from typing import Optional

_START = 0xF0


def _frame(type_byte: int, param: int) -> bytes:
    """Build a 7-byte packet: F0 TYPE PARAM(2) CHECKSUM(3)."""
    body = bytes([type_byte & 0xFF, (param >> 8) & 0xFF, param & 0xFF])
    checksum = bytes(b ^ 0xFF for b in body)          # ones' complement
    return bytes([_START]) + body + checksum


# --------------------------------------------------------------------------- #
# 0x2A — single actions.  Every opcode here is VERIFIED against the app's own
# command table.  Names follow the app's English action labels; a few historical
# aliases are kept so older callers keep working.
# --------------------------------------------------------------------------- #
ACTIONS = {
    'forward':     0x0001,   # move forward
    'turn_left':   0x0004,   # turn left
    'turn_right':  0x0007,   # turn right
    'stop':        0x000A,   # stop
    'swim':        0x000D,   # swimming
    'sit':         0x0010,   # sit down
    'greet':       0x0013,   # greetings / say hello
    'get_down':    0x0016,   # get down
    'act_cute':    0x0019,   # act cute
    'shake_hand':  0x001C,   # handshake
    'attack':      0x001F,   # attack
    'surrender':   0x0022,   # surrender            (was missing in early builds)
    'pee':         0x0025,   # urinate
    'handstand':   0x0028,   # stand / hand-stand
    'patrol':      0x002B,   # patrol
    'kung_fu':     0x002E,   # kung fu
    'push_up':     0x0031,   # push-up
}

# Backwards-compatible aliases (older UI / API names) -> canonical action name.
ALIASES = {
    'squat':     'greet',
    'wag_tail':  'get_down',
    'lie_down':  'act_cute',
    'swing':     'attack',
    'stretch':   'handstand',
    'come_here': 'patrol',
    'prone':     'kung_fu',
}

# INFERRED movement opcodes — these fill the gaps in the action table by the
# regular +3 stride but were NOT observed as explicit literals in the app, so
# treat them as best-effort until confirmed on a physical device.
INFERRED = {
    'backward':    0x0002,
    'left_shift':  0x0005,
    'right_shift': 0x0008,
    'stand_up':    0x0011,
}

# --------------------------------------------------------------------------- #
# Entertainment / audio classes — each has 4 variants (the app plays a random
# one per press).  name -> (TYPE byte, variant count)
# --------------------------------------------------------------------------- #
ENTERTAINMENT = {
    'dance': (0x12, 4),
    'story': (0x18, 4),
    'music': (0x1E, 4),
    'sleep': (0x24, 4),
}

# Feed items (TYPE 0x30), selector 0x0001..0x0008.
FEED_COMMANDS = {
    'feed_water':   0x0001,
    'feed_bone':    0x0002,
    'feed_ice':     0x0003,
    'feed_gem':     0x0004,
    'feed_battery': 0x0005,
    'feed_sun':     0x0006,
    'feed_oil':     0x0007,
    'feed_nucleus': 0x0008,
}

# Full set of single-shot command names encode() understands (2A + inferred +
# entertainment + aliases).  Exposed as COMMANDS for backwards compatibility.
COMMANDS = {**{k: _frame(0x2A, v)[1:3] for k, v in ACTIONS.items()},
            **{k: _frame(0x2A, v)[1:3] for k, v in INFERRED.items()}}


def command_names() -> list:
    """All command names encode() accepts (actions, inferred moves, entertainment, aliases)."""
    return (list(ACTIONS) + list(INFERRED) + list(ENTERTAINMENT) + list(ALIASES))


def encode(command_name: str, variant: Optional[int] = None) -> bytes:
    """
    Encode a single-shot command into a 7-byte BLE packet.

    Handles action commands (0x2A), inferred movement commands, and the
    entertainment classes (dance/story/music/sleep).  For entertainment
    commands, `variant` (1..4) picks a specific clip; the default is variant 1.

    Raises KeyError for an unknown command.

    >>> encode('forward').hex().upper()
    'F02A0001D5FFFE'
    >>> encode('surrender').hex().upper()
    'F02A0022D5FFDD'
    >>> encode('dance').hex().upper()
    'F0120001EDFFFE'
    """
    name = ALIASES.get(command_name, command_name)

    if name in ENTERTAINMENT:
        type_byte, count = ENTERTAINMENT[name]
        v = 1 if variant is None else int(variant)
        if not 1 <= v <= count:
            raise ValueError(f"{name}: variant must be 1..{count}, got {variant}")
        return _frame(type_byte, v)

    if name in ACTIONS:
        return _frame(0x2A, ACTIONS[name])
    if name in INFERRED:
        return _frame(0x2A, INFERRED[name])

    raise KeyError(f"Unknown command: {command_name}. Valid: {command_names()}")


def encode_feed(feed_type: str) -> bytes:
    """Encode a feed command (TYPE 0x30)."""
    if feed_type not in FEED_COMMANDS:
        raise KeyError(f"Unknown feed type: {feed_type}. Valid: {list(FEED_COMMANDS)}")
    return _frame(0x30, FEED_COMMANDS[feed_type])


# --------------------------------------------------------------------------- #
# 0x36 — per-leg posture.  Each leg angle maps to a 0-6 code; the four codes are
# packed as hex nibbles after the TYPE byte, then checksummed the same way.
# --------------------------------------------------------------------------- #
FRONT_LEG_ANGLES = {
    0: 4, -40: 5, -45: 3, -80: 6, -85: 2, 120: 1,
    40: 5, 45: 3, 80: 6, 85: 2,      # absolute value is used, so ± share a code
}
BACK_LEG_ANGLES = {
    0: 3, -45: 4, -50: 2, -75: 5, -90: 1, -110: 6,
    45: 4, 50: 2, 75: 5, 90: 1, 110: 6,
}


def encode_legs(front_left: int, front_right: int, back_left: int, back_right: int) -> bytes:
    """
    Encode a four-leg posture into a 7-byte packet: F0 36 <fl fr bl br> <chk3>.

    Each argument is a leg angle in degrees; see FRONT_LEG_ANGLES / BACK_LEG_ANGLES.
    """
    for lbl, ang, table in (("front left", front_left, FRONT_LEG_ANGLES),
                            ("front right", front_right, FRONT_LEG_ANGLES),
                            ("back left", back_left, BACK_LEG_ANGLES),
                            ("back right", back_right, BACK_LEG_ANGLES)):
        if ang not in table:
            raise ValueError(f"Invalid {lbl} angle: {ang}. Valid: {sorted(set(table))}")

    fl, fr = FRONT_LEG_ANGLES[front_left], FRONT_LEG_ANGLES[front_right]
    bl, br = BACK_LEG_ANGLES[back_left], BACK_LEG_ANGLES[back_right]

    body_hex = f"36{fl}{fr}{bl}{br}"
    body_int = int(body_hex, 16)
    checksum_hex = format(body_int ^ 0xFFFFFF, 'X')
    return bytes.fromhex(f"F0{body_hex}{checksum_hex}")


if __name__ == '__main__':
    # Self-test: every expected packet is checked against the app's own literals.
    cases = [
        ('forward',    'F02A0001D5FFFE'), ('turn_left',  'F02A0004D5FFFB'),
        ('turn_right', 'F02A0007D5FFF8'), ('stop',       'F02A000AD5FFF5'),
        ('swim',       'F02A000DD5FFF2'), ('sit',        'F02A0010D5FFEF'),
        ('greet',      'F02A0013D5FFEC'), ('get_down',   'F02A0016D5FFE9'),
        ('act_cute',   'F02A0019D5FFE6'), ('shake_hand', 'F02A001CD5FFE3'),
        ('attack',     'F02A001FD5FFE0'), ('surrender',  'F02A0022D5FFDD'),
        ('pee',        'F02A0025D5FFDA'), ('handstand',  'F02A0028D5FFD7'),
        ('patrol',     'F02A002BD5FFD4'), ('kung_fu',    'F02A002ED5FFD1'),
        ('push_up',    'F02A0031D5FFCE'),
        # entertainment (variant 1)
        ('dance',      'F0120001EDFFFE'), ('story',      'F0180001E7FFFE'),
        ('music',      'F01E0001E1FFFE'), ('sleep',      'F0240001DBFFFE'),
        # a couple of aliases resolve to the same bytes as their canonical name
        ('squat',      'F02A0013D5FFEC'), ('lie_down',   'F02A0019D5FFE6'),
    ]
    feed = [
        ('feed_water', 'F0300001CFFFFE'), ('feed_bone',    'F0300002CFFFFD'),
        ('feed_ice',   'F0300003CFFFFC'), ('feed_gem',     'F0300004CFFFFB'),
        ('feed_battery','F0300005CFFFFA'),('feed_sun',     'F0300006CFFFF9'),
        ('feed_oil',   'F0300007CFFFF8'), ('feed_nucleus', 'F0300008CFFFF7'),
    ]
    ok = True
    for name, exp in cases:
        got = encode(name).hex().upper()
        ok &= got == exp
        print(f"{'✓' if got == exp else '✗'} {name:12} -> {got} {'' if got == exp else '!= ' + exp}")
    for name, exp in feed:
        got = encode_feed(name).hex().upper()
        ok &= got == exp
        print(f"{'✓' if got == exp else '✗'} {name:12} -> {got} {'' if got == exp else '!= ' + exp}")
    # entertainment variants 1..4 for dance
    for v in range(1, 5):
        print(f"  dance v{v} -> {encode('dance', v).hex().upper()}")
    legs = encode_legs(0, 0, 0, 0).hex().upper()
    ok &= legs == 'F0364433C9BBCC'
    print(f"{'✓' if legs == 'F0364433C9BBCC' else '✗'} legs(0,0,0,0) -> {legs}")
    print("\n" + ("✓ All tests passed!" if ok else "✗ Some tests FAILED"))
