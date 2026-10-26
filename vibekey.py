#!/usr/bin/env python3
"""Custom controls for the Ulanzi AU05 Vibe Key on Linux.

Grabs the AU05's input devices so no app sees its raw keys, then sends our own keys
through a virtual keyboard and wheel.

Each app gets its own set of dial profiles (APP_PROFILES), and the set follows whichever
app is focused. In Terminator the dial steps panes, scrolls, or picks answers; in Obsidian
and Firefox it steps tabs, scrolls, or tabs through the page.

  mode button (the key the AU05 reports as Backspace)
                         next profile in the focused app's set
  Enter + Escape together
                         app switcher (GNOME's Super+Tab). The dial moves between apps;
                         the mode button opens the highlighted app's windows (and closes
                         them again); Enter goes there, Escape cancels. The picked app's
                         profiles then take over.

Run with --debug to print every raw event, which is how to find out what a new key sends.
"""

import argparse
import asyncio
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import evdev
from evdev import UInput, ecodes as e

# --- Tuning -------------------------------------------------------------------------

AU05_VENDOR, AU05_PRODUCT = 0xFFF1, 0x00DD

# What the voice key sends while held, in every profile. The AU05 itself sends Ctrl+Space
# or Ctrl+Alt+Space, depending on its Ulanzi Studio preset. Ctrl+Shift+Space is Handy's
# "Transcribe with Post-Processing": a local model turns "slash commit" into /commit and
# "at" into @ in addresses, at the cost of a few seconds. Plain Ctrl+Space skips that.
VOICE_KEYS = [e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_SPACE]

CTRL, SHIFT, ALT = e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_LEFTALT

# Dial profiles: what one tick up and one tick down send, shown by "name" (default: the
# key). "times" repeats each tick. A "wheel" profile scrolls instead: "pointer" scrolls
# whatever is under the pointer; "window" first moves the pointer onto the focused
# window's centre, and puts it back once the dial rests. "keys" gives Enter or Escape
# another job in that profile: "click" is a left click under the pointer; "delete" erases
# the last Handy dictation on a tap (a single Backspace once that's done) and holds
# Backspace while held.
PROFILES = {
    # Terminator: Ctrl+Tab / Ctrl+Shift+Tab step panes.
    "panes": {"up": [CTRL, SHIFT, e.KEY_TAB], "down": [CTRL, e.KEY_TAB]},
    # Terminator: bind line_up / line_down to these in Preferences > Keybindings, so the
    # focused pane scrolls rather than the one under the pointer.
    "terminal-scroll": {"name": "scroll", "up": [ALT, SHIFT, e.KEY_UP],
                        "down": [ALT, SHIFT, e.KEY_DOWN], "times": 3},
    # Claude Code's multiple-choice questions.
    "choose": {"up": [e.KEY_UP], "down": [e.KEY_DOWN]},
    # Obsidian (and most apps with tabs): Ctrl+Tab / Ctrl+Shift+Tab.
    "tabs": {"up": [CTRL, SHIFT, e.KEY_TAB], "down": [CTRL, e.KEY_TAB]},
    "scroll": {"wheel": "window"},
    # Obsidian: one turn forward makes one new note; one turn back opens the command
    # palette, and further turning moves through it until Enter or Escape.
    "notes": {"once": {"down": [CTRL, e.KEY_N], "up": [CTRL, e.KEY_P]}, "menu": "up"},
    "pointer-scroll": {"name": "scroll under pointer", "wheel": "pointer"},
    # Firefox: Ctrl+PageUp / Ctrl+PageDown follow the tab strip's order (Ctrl+Tab can be
    # set to jump in recently-used order instead).
    "browser-tabs": {"name": "tabs", "up": [CTRL, e.KEY_PAGEUP], "down": [CTRL, e.KEY_PAGEDOWN]},
    # Web pages and Obsidian: Tab / Shift+Tab step through links, buttons and text boxes.
    "fields": {"name": "tab through", "up": [SHIFT, e.KEY_TAB], "down": [e.KEY_TAB],
               "keys": {e.KEY_ENTER: "click", e.KEY_ESC: "delete"}},
}

# App -> its profiles, first one active when the app gets focus. The key is matched
# against the window's WM_CLASS (lowercase substring); run `xprop WM_CLASS` and click a
# window to see an app's. Apps not listed get DEFAULT_PROFILES.
APP_PROFILES = {
    "terminator": ["panes", "terminal-scroll", "choose"],
    "obsidian": ["tabs", "scroll", "notes", "fields"],
    "firefox": ["browser-tabs", "scroll", "fields"],
}
DEFAULT_PROFILES = ["scroll"]

# "window" scrolling: put the pointer back this many seconds after the dial rests.
POINTER_RETURN_S = 0.6
# "once" profiles: ticks closer together than this belong to the same turn.
TURN_REST_S = 0.6

# Flip if turning the dial "forward" goes the wrong way.
INVERT_DIAL = False

# Enter and Escape pressed within this many seconds of each other count as together.
# The AU05 reports the pair 3-70 ms apart, so a lone Enter or Escape waits this long.
CHORD_S = 0.1

# App switcher: the modifier is held while it is open. Ubuntu: Super+Tab switches apps,
# Alt+Tab switches individual windows. Left open this long, it cancels itself, since a
# held Super would turn typing on the real keyboard into shortcuts.
APP_SWITCH_MOD = e.KEY_LEFTMETA
APP_SWITCH_TIMEOUT_S = 20

MODE_KEY = e.KEY_BACKSPACE

# "delete": an Escape held this long holds Backspace instead of erasing the dictation.
HOLD_S = 0.4
# Handy's dictation history. Only a dictation this recent is erased in one go.
HANDY_DIR = Path.home() / ".local/share/com.pais.handy"
HANDY_ERASE_WITHIN_S = 300

# ------------------------------------------------------------------------------------

MODIFIERS = {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_LEFTALT, e.KEY_RIGHTALT,
             e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA}
CHORD_KEYS = (e.KEY_ENTER, e.KEY_ESC)


def last_dictation():
    """(id, text Handy typed) for Handy's newest recent dictation, or None."""
    try:
        db = sqlite3.connect(f"file:{HANDY_DIR / 'history.db'}?mode=ro", uri=True)
        with db:
            row = db.execute(
                "SELECT id, timestamp, transcription_text, post_processed_text"
                " FROM transcription_history ORDER BY id DESC LIMIT 1").fetchone()
        db.close()
        settings = json.loads((HANDY_DIR / "settings_store.json").read_text())
        settings = settings.get("settings", settings)
    except (sqlite3.Error, OSError, ValueError) as err:
        print(f"handy history: {err}", flush=True)
        return None
    if not row or time.time() - row[1] > HANDY_ERASE_WITHIN_S:
        return None
    text = row[3] or row[2]
    if settings.get("append_trailing_space"):
        text += " "
    return row[0], text


def notify(text):
    # The hint makes each notice replace the last instead of stacking.
    subprocess.Popen(["notify-send", "-t", "1200", "-h",
                      "string:x-canonical-private-synchronous:vibekey", "Vibe Key", text],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def profiles_for(app):
    for key, names in APP_PROFILES.items():
        if key in app:
            return key, names
    return "", DEFAULT_PROFILES


class Pointer:
    """Moves the X pointer onto the focused window for "window" scrolling, and back."""

    def __init__(self):
        try:
            from Xlib import X, display
            self.X, self.d = X, display.Display()
            self.root = self.d.screen().root
            self.active_atom = self.d.intern_atom("_NET_ACTIVE_WINDOW")
        except Exception as exc:  # no X display, or python-xlib missing
            print(f"window scrolling falls back to the pointer: {exc}", flush=True)
            self.d = None
        self.saved = None   # where the pointer was before we moved it
        self.placed = None  # where we put it

    def onto_focused_window(self):
        if not self.d or self.saved:
            return
        try:
            prop = self.root.get_full_property(self.active_atom, self.X.AnyPropertyType)
            win = self.d.create_resource_object("window", prop.value[0])
            geo, origin = win.get_geometry(), win.translate_coords(self.root, 0, 0)
            x, y, w, h = -origin.x, -origin.y, geo.width, geo.height
            p = self.root.query_pointer()
            if x <= p.root_x < x + w and y <= p.root_y < y + h:
                return  # already over it
            self.saved, self.placed = (p.root_x, p.root_y), (x + w // 2, y + h // 2)
            self.root.warp_pointer(*self.placed)
            self.d.sync()
        except Exception as exc:
            print(f"could not find the focused window: {exc}", flush=True)

    def back(self):
        if not self.saved:
            return
        try:
            p = self.root.query_pointer()
            if (p.root_x, p.root_y) == self.placed:  # unless the mouse moved meanwhile
                self.root.warp_pointer(*self.saved)
                self.d.sync()
        except Exception:
            pass
        self.saved = self.placed = None


class Remapper:
    def __init__(self, debug):
        self.debug = debug
        self.app, self.profiles = profiles_for("")
        self.index = 0             # into self.profiles
        self.switching = False     # the app switcher is on
        self.switch_open = False   # its popup is up (the switcher's modifier is held)
        self.switch_windows = False  # showing the highlighted app's windows
        self.switch_timer = None   # cancels a switcher left open
        self.refocus = False       # the next focus event resets profiles even for the same app
        self.pending = None        # (key, timer): a lone Enter/Escape waiting for its pair
        self.chorded = set()       # keys whose release belongs to a chord already handled
        self.held_mods = set()     # modifiers the AU05 is holding down right now
        self.voice_down = False
        self.pointer = Pointer()
        self.pointer_timer = None
        self.last_tick = 0.0       # for "once" profiles: when the dial last moved
        self.menu_open = False     # a "once" profile opened a menu the dial now moves through
        self.down = set()          # chord keys physically down
        self.delete_key = None     # the Escape doing a "delete": (key, hold timer or None once held)
        self.erased = None         # id of the Handy dictation already erased
        self.kbd = UInput({e.EV_KEY: list(range(1, 249))}, name="vibekey keyboard")
        # libinput only treats a device as a pointer if it can move and click.
        self.wheel = UInput({
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_WHEEL_HI_RES],
        }, name="vibekey wheel")

    @property
    def profile(self):
        return self.profiles[self.index]

    def label(self):
        name = PROFILES[self.profile].get("name", self.profile)
        return f"{self.app or 'any app'}: {name}"

    def chord(self, keys, value):
        """Press (1) or release (0) keys in order; releases go in reverse."""
        for k in (keys if value else reversed(keys)):
            self.kbd.write(e.EV_KEY, k, value)
        self.kbd.syn()

    def tap(self, keys):
        self.chord(keys, 1)
        self.chord(keys, 0)

    # --- profiles

    def focus_changed(self, wm_class):
        app, profiles = profiles_for(wm_class.lower())
        if (app == self.app and not self.refocus) or self.switching:
            return
        self.app, self.profiles, self.index, self.refocus = app, profiles, 0, False
        self.menu_open = False
        print(f"focus: {wm_class} -> {self.label()}", flush=True)
        if app:
            notify(self.label())

    def next_profile(self):
        self.index = (self.index + 1) % len(self.profiles)
        self.menu_open = False
        print(f"profile: {self.label()}", flush=True)
        notify(self.label())

    # --- the app switcher

    def start_switch(self):
        self.switching = True
        self.touch_switch()
        print("app switcher", flush=True)
        notify("Switch apps: turn the dial · mode button: windows · Enter: go · Esc: cancel")

    def touch_switch(self):
        if self.switch_timer:
            self.switch_timer.cancel()
        self.switch_timer = asyncio.get_running_loop().call_later(
            APP_SWITCH_TIMEOUT_S, lambda: self.end_switch(go=False))

    def switch_step(self, up):
        if not self.switch_open:
            self.chord([APP_SWITCH_MOD], 1)
            self.switch_open = True
        if self.switch_windows:
            self.tap([e.KEY_LEFT] if up else [e.KEY_RIGHT])
        else:
            self.tap([e.KEY_LEFTSHIFT, e.KEY_TAB] if up else [e.KEY_TAB])
        self.touch_switch()

    def switch_toggle_windows(self):
        if not self.switch_open:
            return
        self.switch_windows = not self.switch_windows
        self.tap([e.KEY_DOWN] if self.switch_windows else [e.KEY_UP])
        self.touch_switch()

    def end_switch(self, go):
        """Go to the highlighted app or window (releasing the modifier picks it), or cancel."""
        if self.switch_timer:
            self.switch_timer.cancel()
            self.switch_timer = None
        if self.switch_open:
            if not go:
                self.tap([e.KEY_ESC])
            self.chord([APP_SWITCH_MOD], 0)
        self.switching = self.switch_open = self.switch_windows = False
        # The picked app's focus event arrives shortly; until then keep the old profiles.
        self.refocus = True
        print("app switcher off" + (", switched" if go else ", cancelled"), flush=True)

    # --- input

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
        if code == MODE_KEY:
            if value == 1:
                self.switch_toggle_windows() if self.switching else self.next_profile()
        elif code in CHORD_KEYS:
            self.on_chord_key(code, value)
        elif value == 2:
            self.chord([code], 1)  # autorepeat
        else:
            self.chord(sorted(self.held_mods) + [code], value)

    def on_chord_key(self, code, value):
        if value == 0:
            self.down.discard(code)
            self.chorded.discard(code)
            if self.delete_key and self.delete_key[0] == code:
                self.end_delete()
            return
        if value == 1:
            self.down.add(code)
        if value != 1 or code in self.chorded:
            return
        if self.pending and self.pending[0] != code:
            self.pending[1].cancel()
            self.chorded.update(CHORD_KEYS)
            self.pending = None
            self.end_switch(go=False) if self.switching else self.start_switch()
            return

        def lone():
            self.pending = None
            action = PROFILES[self.profile].get("keys", {}).get(code)
            if self.switching:
                self.end_switch(go=code == e.KEY_ENTER)
            elif action == "click":
                self.click()
            elif action == "delete":
                self.start_delete(code)
            else:
                self.menu_open = False  # Enter runs the highlighted item, Escape closes
                self.tap([code])
        self.pending = (code, asyncio.get_running_loop().call_later(CHORD_S, lone))

    def click(self):
        for value in (1, 0):
            self.wheel.write(e.EV_KEY, e.BTN_LEFT, value)
            self.wheel.syn()

    def start_delete(self, code):
        if code not in self.down:
            self.erase()  # already released: a tap
            return

        def held():
            self.delete_key = (code, None)
            self.chord([e.KEY_BACKSPACE], 1)  # X repeats it until the release
        timer = asyncio.get_running_loop().call_later(HOLD_S - CHORD_S, held)
        self.delete_key = (code, timer)

    def end_delete(self):
        timer = self.delete_key[1]
        self.delete_key = None
        if timer:
            timer.cancel()
            self.erase()
        else:
            self.chord([e.KEY_BACKSPACE], 0)

    def erase(self):
        """Erase the last Handy dictation if it hasn't been yet, else one character."""
        last = last_dictation()
        if last and last[0] != self.erased:
            self.erased = last[0]
            print(f"erasing dictation {last[0]} ({len(last[1])} characters)", flush=True)
            for _ in last[1]:
                self.tap([e.KEY_BACKSPACE])
        else:
            self.tap([e.KEY_BACKSPACE])

    def scroll(self, code, value, where):
        if where == "window":
            self.pointer.onto_focused_window()
            if self.pointer_timer:
                self.pointer_timer.cancel()
            self.pointer_timer = asyncio.get_running_loop().call_later(POINTER_RETURN_S, self.pointer.back)
        self.wheel.write(e.EV_REL, code, value)
        self.wheel.syn()

    def on_wheel(self, code, value):
        spec = PROFILES[self.profile]
        if code != e.REL_WHEEL:
            # Hi-res twin of each tick; only wheel profiles pass it on.
            if not self.switching and "wheel" in spec:
                self.scroll(code, value, spec["wheel"])
            return
        up = (value > 0) != INVERT_DIAL
        if self.switching:
            for _ in range(abs(value)):
                self.switch_step(up)
            return
        if "wheel" in spec:
            self.scroll(code, value, spec["wheel"])
            return
        if "once" in spec:
            now = asyncio.get_running_loop().time()
            new_turn, self.last_tick = now - self.last_tick > TURN_REST_S, now
            if self.menu_open:
                for _ in range(abs(value)):
                    self.tap([e.KEY_UP] if up else [e.KEY_DOWN])
            elif new_turn:
                way = "up" if up else "down"
                self.tap(spec["once"][way])
                self.menu_open = spec.get("menu") == way
            return
        for _ in range(abs(value) * spec.get("times", 1)):
            self.tap(spec["up"] if up else spec["down"])

    def handle(self, dev, ev):
        if self.debug and ev.type != e.EV_SYN:
            print(f"{dev.name}: {evdev.categorize(ev)}", flush=True)
        if ev.type == e.EV_KEY:
            self.on_key(ev.code, ev.value)
        elif ev.type == e.EV_REL and ev.code in (e.REL_WHEEL, e.REL_WHEEL_HI_RES):
            self.on_wheel(ev.code, ev.value)


async def watch_focus(remapper):
    """Follow the focused window's WM_CLASS through `xprop -spy` on the root window."""
    while True:
        try:
            spy = await asyncio.create_subprocess_exec(
                "xprop", "-spy", "-root", "_NET_ACTIVE_WINDOW",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except FileNotFoundError:
            print("xprop not found; app profiles are off", flush=True)
            return
        async for line in spy.stdout:
            m = re.search(rb"window id # (0x[0-9a-f]+)", line)
            if not m or m.group(1) == b"0x0":
                continue
            probe = await asyncio.create_subprocess_exec(
                "xprop", "-id", m.group(1).decode(), "WM_CLASS",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await probe.communicate()
            names = re.findall(r'"([^"]*)"', out.decode(errors="replace"))
            if names:
                remapper.focus_changed(" ".join(names))
        await spy.wait()
        await asyncio.sleep(2)  # X went away (logout); try again


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
    asyncio.create_task(watch_focus(remapper))
    print(f"vibekey running; {remapper.label()}", flush=True)
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
