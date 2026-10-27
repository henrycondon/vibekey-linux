# vibekey-linux

Unofficial Linux remapper for the **Ulanzi AU05 Vibe Key** voice-input keypad. It is not affiliated with Ulanzi.

Ulanzi Studio only runs on Windows and macOS. On Linux, the AU05's USB dongle already shows up as a standard keyboard, mouse wheel and microphone, so no driver is needed. What's missing is control over what each input does. `vibekey.py` grabs the AU05's inputs so apps never see the raw keys, then sends your own keys through a virtual keyboard and wheel.

## What it does

Each app gets its own set of dial profiles, and the set follows whichever app is in front, the way Ulanzi Studio links profiles to apps.

| App | Dial profiles (the mode button steps through them) |
|---|---|
| Terminator | **panes** (Ctrl+Tab / Ctrl+Shift+Tab) → **scroll** the focused pane (Alt+Shift+Up/Down, 3 lines per tick; confirm is Enter on a tap and Tab when held), which on entry fills the window with that pane (Ctrl+Shift+X) and makes its text `ZOOM_STEPS` points bigger (Ctrl+plus), and keeps both through **edit** and **choose**, undoing them when the mode button gets back to **panes** → **edit** (Left/Right move the cursor; confirm is Space on a tap and Enter when held; cancel is Backspace, held to keep deleting) → **choose** (Up/Down, for Claude Code's multiple-choice questions) |
| Obsidian | **tabs** (Ctrl+Tab / Ctrl+Shift+Tab) → **scroll** the focused window → **notes**: a turn forward makes one new note (Ctrl+N); a turn back opens the command palette (Ctrl+P), and further turning moves through it until Enter or Escape → **tab through** (as in Firefox; Tab indents inside a note) |
| Firefox | **tabs** (Ctrl+PageUp / Ctrl+PageDown) → **scroll** the focused window → **tab through** (Tab / Shift+Tab, to reach links, buttons and text boxes; Enter left-clicks under the pointer and Escape deletes, see below) |
| Anything else | **scroll** the focused window |

| AU05 input | Action |
|---|---|
| Mode button (reported as Backspace) | Next profile in the focused app's set, with a desktop notification |
| Enter + Escape together | App switcher (GNOME's Super+Tab): the dial moves between apps; the mode button opens the highlighted app's windows, where the dial moves between them, and closes them again; Enter goes there, Escape (or the pair again) cancels. The picked app's profiles take over. A switcher left open 20 s cancels itself |
| Voice key | Holds Ctrl+Alt+Shift+Space, bound in Handy to "Transcribe with Post-Processing" (Handy's default Ctrl+Shift+Space clashes with the Hermes desktop app) (set `VOICE_KEYS` to Ctrl+Space for plain dictation) |
| Escape in **tab through** | A tap erases your last Handy dictation in one go (if it's under 5 minutes old and not erased yet), and after that deletes one character. Holding it holds Backspace |
| Enter / Escape alone | Enter / Escape, sent after a 0.1 s wait to check whether its partner follows |

Profiles and app sets are set in `PROFILES` and `APP_PROFILES` at the top of `vibekey.py`. Apps are matched by window class: run `xprop WM_CLASS` and click a window to see one. Focus is tracked through `xprop`, so this needs an X11 session.

Apps without a keyboard scroll command get **window scrolling**: while the dial turns, the pointer moves to the centre of the focused window, and it goes back 0.6 s after the dial stops (unless you moved the mouse in the meantime). In a split layout it scrolls whatever is at the window's centre. Use the `pointer-scroll` profile to scroll under the pointer instead.

## Before you start: set up the keypad once on Windows or macOS

A brand-new AU05 needs to be set up once in Ulanzi Studio. Until then, its dongle reboots every few seconds as soon as the keypad wakes up. After one setup the keypad keeps its preset, and the dongle works on Linux from then on.

Plug the dongle directly into a USB port. Through an adapter, it kept dropping out.

## Install

```sh
git clone https://github.com/henrycondon/vibekey-linux ~/Desktop/repos/vibekey-linux
cd ~/Desktop/repos/vibekey-linux
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt   # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Let the logged-in user read the AU05 and create virtual input devices
sudo cp 70-vibekey.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger

# Try it; --debug prints every raw event, which shows you what each key sends
.venv/bin/python vibekey.py --debug

# Run it at every login
ln -s "$PWD/vibekey.service" ~/.config/systemd/user/vibekey.service
systemctl --user daemon-reload
systemctl --user enable --now vibekey
```

### Terminator setup

Terminator has keyboard scroll actions but leaves them unbound. In **Preferences → Keybindings**, set `line_up` to Alt+Shift+Up and `line_down` to Alt+Shift+Down. With scroll mode on, you can turn the dial one notch to enter each one. Raising **Profiles → Scrolling → Scrollback** above the default 500 lines also helps.

To scroll whatever is under the pointer instead, use the `wheel` profile in Terminator's set.

`vibekey.service` expects the repo at `~/Desktop/repos/vibekey-linux`. If you clone it somewhere else, edit `ExecStart`.

After changing a setting, run `systemctl --user restart vibekey`. Logs: `journalctl --user -u vibekey -f`.

## Notes

- The udev rule uses `uaccess`, so access applies only to the user logged in at the machine. The same rule also opens `/dev/uinput` to that user, which lets any program they run create virtual input devices. Tools like input-remapper and keyd need the same access.
- Tested on Ubuntu 22.04 (GNOME, X11) with AU05 USB ID `fff1:00dd`.

## License

MIT
