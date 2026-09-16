"""Drive the real click_effect loop with scripted buttons.

The spin latch is the most intricate state in this project and none of it was
provable: the self-test can exercise the cursor swaps, but it cannot press a
mouse button. So this fakes GetAsyncKeyState with a timeline, records what the
loop DOES instead of what it draws, and asserts the sequence.

It runs the shipped loop, not a copy of the logic.

    python test_latch.py
"""

import ctypes
import sys
import threading
import time

import click_effect as CE


class Done(Exception):
    pass


def run_timeline(steps, tick=0.02):
    """steps: list of (seconds, left_down, right_down). Returns the action log."""
    actions = []
    state = {"lb": False, "rb": False}

    # A timeline rather than real input. Each entry holds the buttons in that
    # position for its duration, so a "click" is a down entry then an up entry.
    def player():
        try:
            for seconds, lb, rb in steps:
                state["lb"], state["rb"] = lb, rb
                time.sleep(seconds)
        finally:
            state["stop"] = True

    def fake_key(vk):
        if state.get("stop"):
            raise Done()
        if vk == CE.VK_LBUTTON:
            return -32768 if state["lb"] else 0
        if vk == CE.VK_RBUTTON:
            return -32768 if state["rb"] else 0
        return 0

    real = {
        "key": CE.u32.GetAsyncKeyState,
        "press": CE.press,
        "release": CE.release,
        "set_all": CE.set_all,
        "pidfile": CE.PIDFILE,
    }
    t0 = time.time()
    CE.u32.GetAsyncKeyState = fake_key
    CE.press = lambda *_a: actions.append((time.time() - t0, "press"))
    CE.release = lambda *_a: actions.append((time.time() - t0, "release"))
    CE.set_all = lambda path, _ids: actions.append(
        (time.time() - t0, "spin%s" % path[-5:-4]))   # spinN.ani -> N
    CE.PIDFILE = CE.PIDFILE + ".test"

    t = threading.Thread(target=player)
    t.start()
    try:
        CE.run()
    except (Done, SystemExit):
        pass
    finally:
        t.join()
        CE.u32.GetAsyncKeyState = real["key"]
        CE.press, CE.release, CE.set_all = real["press"], real["release"], real["set_all"]
        CE.PIDFILE = real["pidfile"]
    return actions


def during(actions, lo, hi):
    """What happened strictly inside a time window. Order alone cannot tell a
    stop-click apart from the click that follows it; timing can."""
    return [name for t, name in actions if lo <= t < hi]


def names(actions):
    out = []
    for _t, a in actions:
        if not out or out[-1] != a:
            out.append(a)
    return out


def main():
    checks = []

    # Timeline, in seconds from start:
    #   0.00-0.10  nothing
    #   0.10-0.80  both buttons down   (past the 0.35s arm, so it latches)
    #   0.80-1.40  ALL buttons up      <- the spin must survive this entirely
    #   1.40-1.55  one left click      <- this must stop it
    #   1.55-1.85  nothing
    log = run_timeline([
        (0.10, False, False),
        (0.70, True, True),
        (0.60, False, False),
        (0.15, True, False),
        (0.30, False, False),
    ])
    seq = names(log)

    checks.append(("spin starts while both are held",
                   any(a.startswith("spin") for a in during(log, 0.10, 0.80))))

    # The decisive one. Letting go is not a click, so nothing at all may happen
    # in the hands-off window. Order could not distinguish this from the stop.
    idle = during(log, 0.85, 1.38)
    checks.append(("hands off changes nothing (%s)" % (idle or "silent"),
                   idle == []))

    checks.append(("the next click stops it",
                   "release" in during(log, 1.38, 1.60)))

    # A quick both-down blip under the arm time must press, never spin.
    log2 = run_timeline([
        (0.10, False, False),
        (0.20, True, True),      # 0.20s, well under 0.35s
        (0.40, False, False),
    ])
    seq2 = names(log2)
    checks.append(("a blip under the arm time does not spin",
                   not any(a.startswith("spin") for a in seq2)))
    checks.append(("a blip still shows the pressed artwork", "press" in seq2))

    bad = 0
    for label, ok in checks:
        print("  %-44s %s" % (label, "PASS" if ok else "FAIL"))
        bad += 0 if ok else 1
    print("  timeline 1: %s" % " -> ".join(seq))
    print("  timeline 2: %s" % " -> ".join(seq2))
    if bad:
        sys.exit("%d latch check(s) failed" % bad)
    print("spin latch verified against the real loop")


if __name__ == "__main__":
    main()
