# Bringing vibekey to macOS: handoff for Claude on the Mac

> **Prompt to paste into Claude Code on the Mac:**
> Read MAC.md and vibekey.py in https://github.com/henrycondon/vibekey-linux. vibekey.py is a working Linux remapper for my Ulanzi AU05 Vibe Key. Recreate the same behaviour on this Mac, following MAC.md. Check every item marked VERIFY on this machine before relying on it. Before you change Ulanzi Studio's files, quit the app and back up its data folder.

## What exists on Linux

`vibekey.py` grabs the AU05's input devices so apps never see its raw keys. It sends its own keys through a virtual keyboard, which makes these features possible:

- per-app dial profiles that follow the focused app;
- a mode button that steps through the focused app's profiles;
- a chord (confirm + cancel pressed together) that opens the app switcher;
- scrolling the focused window rather than the window under the pointer.

The Python source is the reference for every behaviour below.

### What the AU05 sends, using the preset made in Ulanzi Studio on the Mac

| Control | Sends |
|---|---|
| Dial | Mouse wheel (one tick = one notch) |
| Mode button | Backspace |
| Confirm | Enter |
| Cancel | Escape |
| Voice key | Ctrl+Space while held (dictation app: Handy) |

The USB dongle is vendor `0xFFF1`, product `0x00DD` (65521 / 221 in decimal).

### Profiles, per app

The first profile in each list is active when the app gets focus. The mode button goes to the next one, wrapping around, and shows a notification such as "obsidian: notes". In each profile, the first key is sent for dial up/back and the second for dial down/forward.

| App | Profiles in order |
|---|---|
| Terminal (Terminator on Linux) | **panes**: previous/next pane · **scroll**: scroll the focused pane 3 lines per tick; confirm taps Enter, holds Tab · **edit**: Left / Right move the cursor; confirm taps Space, holds Enter (send); cancel is Backspace, held to repeat · **choose**: Up / Down, for Claude Code's multiple-choice questions |
| Obsidian | **tabs**: Ctrl+Shift+Tab / Ctrl+Tab · **panes**: focus the split pane to the left / right (the "Focus on tab group" commands, bound to Alt+Shift+Left/Right in each vault) · **scroll**: scroll the focused window · **notes**: see below · **tab through**: Shift+Tab / Tab |
| Firefox | **tabs**: previous/next tab in tab-strip order (Ctrl+PageUp / Ctrl+PageDown) · **scroll**: scroll the focused window · **tab through**: Shift+Tab / Tab |
| Any other app | **scroll**: scroll the focused window |

Special behaviours:

- **Window scrolling.** Apps scroll whatever is under the pointer. So while the dial turns, the pointer jumps to the centre of the focused window. It jumps back 0.6 s after the dial rests, unless the user moved the mouse in the meantime.
- **notes (Obsidian).** A forward turn makes exactly one new note, however many ticks the turn has. Ticks less than 0.6 s apart count as one turn. A back turn opens the command palette (Cmd+P on the Mac). From then on the dial sends Up/Down through the palette until a lone Enter or Escape.
- **tab through.** In this profile only:
  - Confirm is a **left click** under the pointer.
  - Cancel is a **delete** key:
    - A tap erases the user's last Handy dictation in one go: it sends as many Backspaces as the dictation has characters, read from Handy's history database. This only happens if the dictation is under 5 minutes old and hasn't been erased already. Otherwise a tap deletes one character.
    - Holding cancel for 0.4 s or longer holds Backspace down, so it repeats.
- **Terminal zoom.** Entering **scroll** makes the focused pane fill the window and sets its text 6 pt above normal. On Linux that's Ctrl+Shift+X, then Ctrl+0, then Ctrl+plus six times. The zoom stays through **edit** and **choose**. Stepping on to **panes** resets the text (Ctrl+0) and the layout (Ctrl+Shift+X again).
  - Terminator's zoom is a toggle, so vibekey remembers a zoom it still has to undo in `~/.cache/vibekey/owed.json`, which survives restarts.
  - It undoes the zoom only while the terminal is focused. If you switch apps while zoomed, the zoom is undone when you come back, and the profile restarts at panes.
  - Linux now checks the real state before toggling: `terminator_panes.py` counts the window's visible panes over the accessibility bus. On the Mac, Hammerspoon's `hs.axuielement` could do the same.
  - On the Mac, prefer an idempotent zoom if the terminal has one, such as iTerm2's "Maximize Active Pane" plus a font-size action. VERIFY.
- **Tap versus hold.** A key counts as held after 0.4 s. A hold action fires once at that moment, except the held Backspaces, which repeat until release.
- **Lone confirm and cancel.** Everywhere else they send Enter and Escape. Each one is held back 0.1 s first, to see whether its partner follows (the chord below).

### The app switcher (confirm + cancel together)

The AU05 reports the two keys 3-70 ms apart, so "together" means within 0.1 s.

1. The chord opens the switcher. On Linux this is GNOME's Super+Tab, with Super held.
2. The dial moves between apps with Tab and Shift+Tab, while the modifier stays held.
3. The mode button opens the highlighted app's window list (Down), where the dial moves between windows (Left/Right). Pressing the mode button again closes the list (Up).
4. A lone confirm goes to the highlighted app or window. A lone cancel, or the chord again, cancels.
5. The switcher never picks by itself after a pause. It only cancels after 20 s, as a safety net, because a stuck modifier would turn real typing into shortcuts.
6. After switching, the newly focused app's profiles take over, even if the user picked the same app again.

User preferences learned along the way:

- No "hold a button and turn" combinations. The AU05 handles them badly.
- The voice key always sends the dictation shortcut, in every profile.
- Don't present commands as tables. Give them one per line, so they can be copied.

## The macOS problem and a suggested design

Ulanzi Studio can map each control to a hotkey and link profiles to apps. It probably can't do a two-key chord, keep a profile index that the mode button steps through, warp the pointer, or read Handy's history (VERIFY). So split the work:

1. **Ulanzi Studio as a dumb sender.** Give each AU05 control a unique, otherwise-unused key, the same in every Ulanzi profile. For example: dial left F13, dial right F14, mode F15, confirm F16, cancel F17, and the voice key unchanged (Ctrl+Space or whatever the Mac dictation app uses).
   - VERIFY that Ulanzi Studio lets the dial's rotate-left and rotate-right send a hotkey. If they can only scroll, the remapper can't tell the AU05 wheel from the real mouse. In that case use Karabiner-Elements, which can match `vendor_id 65521 / product_id 221`, or read the device directly with hidapi.
2. **Hammerspoon as the brain** (Lua; or Karabiner-Elements plus Hammerspoon). Port `vibekey.py`'s Remapper:
   - `hs.application.watcher` for focus;
   - `hs.eventtap` to catch F13-F17 and send the real keys;
   - `hs.timer` for the 0.1 s chord window, the 0.6 s turn rest and the 20 s switcher timeout;
   - `hs.mouse.absolutePosition` plus `hs.window.focusedWindow():frame()` for window scrolling;
   - `hs.eventtap.event.newScrollEvent` to scroll;
   - `hs.notify` or `hs.alert` for the profile name;
   - `hs.sqlite3` (or a shell call to `sqlite3`) for Handy's history.

   Hammerspoon needs Accessibility permission.
3. **Optional: editing Ulanzi Studio's files directly.** Find its data folder (VERIFY: likely under `~/Library/Application Support/`, with a name containing Ulanzi). Quit the app, back up the folder, then write the F13-F17 mappings into the profile JSON if editing in the GUI is tedious. Ulanzi also has an official plugin SDK (github.com/UlanziTechnology/UlanziDeckPlugin-SDK). It's an option, but a hotkey sender plus Hammerspoon is simpler.

### Mac key equivalents (VERIFY each one in the app)

| Action | Linux key | Likely Mac key |
|---|---|---|
| iTerm2 next / previous pane | Ctrl+Tab / Ctrl+Shift+Tab (Terminator) | Cmd+] / Cmd+[ |
| iTerm2 scroll one line | Alt+Shift+Up/Down (bound in Terminator) | Cmd+Up / Cmd+Down; bind it in Settings → Keys if unbound |
| Obsidian new note / command palette | Ctrl+N / Ctrl+P | Cmd+N / Cmd+P |
| Obsidian next / previous tab | Ctrl+Tab / Ctrl+Shift+Tab | the same |
| Firefox next / previous tab | Ctrl+PageDown / Ctrl+PageUp | the same, or Cmd+Option+Right / Left |
| App switcher | hold Super, Tab / Shift+Tab | hold Cmd, Tab / Shift+Tab |
| Switcher: show the app's windows | Down, then Left/Right | Down arrow while Cmd+Tab is up (App Exposé), then arrow keys. VERIFY whether picking needs Return there rather than releasing Cmd |
| Handy history | `~/.local/share/com.pais.handy/history.db` | VERIFY: probably `~/Library/Application Support/com.pais.handy/history.db`. Table `transcription_history`: newest row by `id`; the typed text is `post_processed_text` if set, else `transcription_text`; `timestamp` is in Unix seconds |

### Testing

Build it in small steps, then:

1. Watch Hammerspoon's console while pressing each AU05 control, to confirm the F-keys arrive.
2. Then the profiles.
3. Then the chord switcher.
4. Then window scrolling and the Handy erase.
