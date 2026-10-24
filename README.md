# vibekey-linux

Unofficial Linux remapper for the **Ulanzi AU05 Vibe Key** voice-input keypad. It is not affiliated with Ulanzi.

Ulanzi Studio only runs on Windows and macOS. On Linux, the AU05's USB dongle already shows up as a standard keyboard, mouse wheel and microphone, so no driver is needed. What's missing is control over what each input does. `vibekey.py` grabs the AU05's inputs so apps never see the raw keys, then sends your own keys through a virtual keyboard and wheel.

## What it does

| AU05 input | Action |
|---|---|
| Backspace key | Toggle the dial between **scroll** and **panes** mode, with a desktop notification |
| Dial, scroll mode | Scroll the **focused** pane, wherever the pointer is (sends Alt+Shift+Up/Down, 3 lines per tick) |
| Dial, panes mode | Next or previous terminal pane, one per tick (Terminator's Ctrl+Tab / Ctrl+Shift+Tab by default) |
| Voice key | Holds Ctrl+Space for a dictation app such as Handy |
| Enter / Escape | Enter / Escape |

Every key, the pane shortcuts, dial sensitivity and direction are set in the constants at the top of `vibekey.py`. Zellij users can swap in Alt+PageDown / Alt+PageUp.

## Before you start: set up the keypad once on Windows or macOS

A brand-new AU05 needs to be set up once in Ulanzi Studio. Until then, its dongle reboots every few seconds as soon as the keypad wakes up. After one setup the keypad keeps its preset, and the dongle works on Linux from then on.

Plug the dongle directly into a USB port. Through an adapter, it kept dropping out.

## Install

```sh
git clone https://github.com/henrycondon/vibekey-linux ~/Desktop/repos/vibekey-linux
cd ~/Desktop/repos/vibekey-linux
uv venv .venv && uv pip install --python .venv/bin/python evdev   # or: python3 -m venv .venv && .venv/bin/pip install evdev

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

To scroll whatever is under the pointer instead, set `SCROLL_UP = None` in `vibekey.py`.

`vibekey.service` expects the repo at `~/Desktop/repos/vibekey-linux`. If you clone it somewhere else, edit `ExecStart`.

After changing a setting, run `systemctl --user restart vibekey`. Logs: `journalctl --user -u vibekey -f`.

## Notes

- The udev rule uses `uaccess`, so access applies only to the user logged in at the machine. The same rule also opens `/dev/uinput` to that user, which lets any program they run create virtual input devices. Tools like input-remapper and keyd need the same access.
- Tested on Ubuntu 22.04 (GNOME, X11) with AU05 USB ID `fff1:00dd`.

## License

MIT
