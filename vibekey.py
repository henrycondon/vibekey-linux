#!/usr/bin/env python3
"""Custom controls for the Ulanzi AU05 Vibe Key on Linux.

Grabs the AU05's input devices so no app sees its raw keys, then sends our own keys
through a virtual keyboard and wheel. The dial has two modes, toggled by the key the
AU05 reports as Backspace:

  scroll  the dial scrolls, as it does without this script
  panes   the dial steps through terminal panes (Terminator's Ctrl+Tab / Ctrl+Shift+Tab)

Run with --debug to print every raw event, which is how to find out what a new key sends.
"""

import argparse
import asyncio
import subprocess
import sys

import evdev
from evdev import UInput, ecodes as e

# --- Tuning -------------------------------------------------------------------------

AU05_VENDOR, AU05_PRODUCT = 0xFFF1, 0x00DD

# What the voice key sends while held, in both dial modes. The AU05 itself sends Ctrl+Alt+Space.
VOICE_KEYS = [e.KEY_LEFTCTRL, e.KEY_SPACE]

# Keys sent to step panes. Terminator: next = Ctrl+Tab, previous = Ctrl+Shift+Tab.
# For Zellij use [e.KEY_LEFTALT, e.KEY_PAGEDOWN] and [e.KEY_LEFTALT, e.KEY_PAGEUP].
NEXT_PANE = [e.KEY_LEFTCTRL, e.KEY_TAB]
PREV_PANE = [e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_TAB]

# Scroll mode sends these keys so the focused pane scrolls, not the one under the pointer.
# Bind them in Terminator: Preferences > Keybindings > line_up / line_down.
# Set to None to send plain wheel scroll instead.
SCROLL_UP = [e.KEY_LEFTALT, e.KEY_LEFTSHIFT, e.KEY_UP]
SCROLL_DOWN = [e.KEY_LEFTALT, e.KEY_LEFTSHIFT, e.KEY_DOWN]
# Lines scrolled per dial tick.
LINES_PER_TICK = 3

# The dial sends many wheel ticks per turn; step one pane per this many ticks.
TICKS_PER_PANE = 1
# Forget a partial step after this many seconds without dial movement.
TICK_RESET_S = 0.4
# Flip if turning the dial "forward" goes to the previous pane.
INVERT_DIAL = False

# AU05 key -> action. Actions: "mode" toggles the dial mode; a list of keys is sent as
# a chord. Keys not listed here pass straight through.
KEYMAP = {
    e.KEY_BACKSPACE: "mode",
    e.KEY_ENTER: [e.KEY_ENTER],
    e.KEY_ESC: [e.KEY_ESC],
}

MODES = ["scroll", "panes"]

# ------------------------------------------------------------------------------------

MODIFIERS = {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTALT, e.KEY_RIGHTALT,
             e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA}


def notify(text):
    # The hint makes each notice replace the last instead of stacking.
    subprocess.Popen(["notify-send", "-t", "1200", "-h",
                      "string:x-canonical-private-synchronous:vibekey", "Vibe Key", text],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Remapper:
    def __init__(self, debug):
        self.debug = debug
        self.mode = MODES[0]
        self.held_mods = set()   # modifiers the AU05 is holding down right now
        self.voice_down = False
        self.ticks = 0
        self.last_tick = 0.0
        self.kbd = UInput({e.EV_KEY: list(range(1, 249))}, name="vibekey keyboard")
        # libinput only treats a device as a pointer if it can move and click.
        self.wheel = UInput({
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_WHEEL_HI_RES],
        }, name="vibekey wheel")

    def chord(self, keys, value):
        """Press (1) or release (0) keys in order; releases go in reverse."""
        for k in (keys if value else reversed(keys)):
            self.kbd.write(e.EV_KEY, k, value)
        self.kbd.syn()

    def tap(self, keys):
        self.chord(keys, 1)
        self.chord(keys, 0)

    def on_key(self, code, value):
        if code in MODIFIERS:
            # Held back: the AU05 only uses modifiers as part of the voice chord.
            (self.held_mods.add if value else self.held_mods.discard)(code)
            return
        if code == e.KEY_SPACE and (self.held_mods or self.voice_down):
            if value == 1 and not self.voice_down:
                self.voice_down = True
                self.chord(VOICE_KEYS, 1)
            elif value == 0 and self.voice_down:
                self.voice_down = False
                self.chord(VOICE_KEYS, 0)
            return
        action = KEYMAP.get(code)
        if action == "mode":
            if value == 1:
                self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
                self.ticks = 0
                print(f"dial mode: {self.mode}", flush=True)
                notify(f"Dial: {self.mode}")
        elif action is not None:
            if value == 1:
                self.tap(action)
        elif value == 2:
            self.chord([code], 1)  # autorepeat
        else:
            self.chord(sorted(self.held_mods) + [code], value)

    def on_wheel(self, code, value):
        if self.mode == "scroll":
            if SCROLL_UP is None:
                self.wheel.write(e.EV_REL, code, value)
                self.wheel.syn()
            elif code == e.REL_WHEEL:
                keys = SCROLL_UP if value > 0 else SCROLL_DOWN
                for _ in range(abs(value) * LINES_PER_TICK):
                    self.tap(keys)
            return
        if code != e.REL_WHEEL:
            return
        now = asyncio.get_running_loop().time()
        if now - self.last_tick > TICK_RESET_S:
            self.ticks = 0
        self.last_tick = now
        self.ticks += -value if INVERT_DIAL else value
        while abs(self.ticks) >= TICKS_PER_PANE:
            # Wheel down (negative) is the dial's forward direction.
            self.tap(NEXT_PANE if self.ticks < 0 else PREV_PANE)
            self.ticks += TICKS_PER_PANE if self.ticks < 0 else -TICKS_PER_PANE

    def handle(self, dev, ev):
        if self.debug and ev.type != e.EV_SYN:
            print(f"{dev.name}: {evdev.categorize(ev)}", flush=True)
        if ev.type == e.EV_KEY:
            self.on_key(ev.code, ev.value)
        elif ev.type == e.EV_REL and ev.code in (e.REL_WHEEL, e.REL_WHEEL_HI_RES):
            self.on_wheel(ev.code, ev.value)


def find_au05():
    devs = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if (d.info.vendor, d.info.product) == (AU05_VENDOR, AU05_PRODUCT):
            devs.append(d)
        else:
            d.close()
    return devs


async def read_device(dev, remapper):
    try:
        async for ev in dev.async_read_loop():
            remapper.handle(dev, ev)
    except OSError:
        pass  # unplugged or the dongle re-enumerated
    finally:
        try:
            dev.close()
        except OSError:
            pass


async def main(debug):
    remapper = Remapper(debug)
    print(f"vibekey running; dial mode: {remapper.mode}", flush=True)
    while True:
        devs = find_au05()
        if not devs:
            await asyncio.sleep(1)
            continue
        for d in devs:
            d.grab()
        print("AU05 attached: " + ", ".join(d.name for d in devs), flush=True)
        await asyncio.gather(*(read_device(d, remapper) for d in devs))
        print("AU05 detached; waiting for it", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--debug", action="store_true", help="print every raw AU05 event")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.debug))
    except PermissionError as exc:
        sys.exit(f"{exc}\nNo access to the AU05 or /dev/uinput; install 70-vibekey.rules (see README.md).")
    except KeyboardInterrupt:
        pass
