#!/usr/bin/python3
"""Print how many terminals the focused Terminator window shows.

Terminator's zoom removes the other terminals from the window, so 1 means zoomed (or a
window with a single pane). vibekey.py runs this with the system Python, which has
pyatspi (python3-pyatspi), to make its zoom independent of what it remembers.
Prints nothing and exits 1 if there is no answer.
"""

import sys

import pyatspi


def count(acc, depth=0):
    if depth > 40:
        return 0
    if acc.getRoleName() == "terminal":
        return 1 if acc.getState().contains(pyatspi.STATE_SHOWING) else 0
    return sum(count(child, depth + 1) for child in acc if child)


for app in pyatspi.Registry.getDesktop(0):
    if app and app.name and "terminator" in app.name.lower():
        for frame in app:
            if frame and frame.getState().contains(pyatspi.STATE_ACTIVE):
                print(count(frame))
                sys.exit(0)
sys.exit(1)
