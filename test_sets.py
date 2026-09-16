"""Prove that adding a cursor set is ONE edit, not five.

Every defect this file guards against had the same shape: the list of sets
lived in more than one place, so a new set was half-added and the half that was
missing failed silently.

  * the panel drew ARROW in the gallery with no button to pick it, because the
    button row was a hard-coded pair
  * install() named the ARROW scheme "G9 Reticle", because the name came from
    a two-way "8 Star or else Reticle"
  * a settings file naming a set that had been renamed away took the whole
    engine down, because nothing checked the name was still real

So: build_cursors.VARIANTS is the source. Everything else is checked against
it, and the gesture is actually walked rather than reasoned about.

    python test_sets.py
"""

import contextlib
import io
import json
import os
import subprocess
import sys

import build_cursors as B
import click_effect as CE
import cursor_cli as CLI
import install_cursors as I

ROLE_COUNT = len(B.ROLES_STATIC) + len(B.ROLES_ANI)
EXPECT_PER_SET = ROLE_COUNT * 2 + len(B.SPIN_FRAMES)     # resting + pressed + spin


def installed_now():
    cfg = {k: v["value"] for k, v in I.read_current().items()}
    parts = cfg.get("Arrow", "").replace("/", "\\").split("\\")
    return parts[parts.index("cursors") + 1] if "cursors" in parts else None


def main():
    checks = []
    sets = list(B.VARIANTS)

    checks.append(("more than one set exists to cycle through", len(sets) >= 2))

    # 1. Artwork. A set in the list with no files on disk is the failure that
    #    looks like "the gesture skips one".
    for v in sets:
        d = os.path.join(B.OUT_DIR, v)
        n = sum(1 for _r, _d, fs in os.walk(d) for f in fs
                if f.lower().endswith((".cur", ".ani"))) if os.path.isdir(d) else 0
        checks.append(("%-9s has its full artwork: %d files" % (v, n),
                       n == EXPECT_PER_SET))

    # 2. Names. Two sets sharing a scheme name means one silently overwrites
    #    the other in Mouse Properties.
    names = [I.scheme_name(v) for v in sets]
    checks.append(("every set has its own scheme name: %s" % ", ".join(names),
                   len(set(names)) == len(sets)))

    labels = [CLI.label_for(v) for v in sets]
    checks.append(("every set has its own panel label: %s" % ", ".join(labels),
                   len(set(labels)) == len(sets) and all(labels)))

    # 3. The panel builds its buttons from what status publishes, so status
    #    must publish every set. This is the check that would have caught the
    #    missing ARROW button.
    #    Run the real command, not a reconstruction of it: a check that
    #    rebuilds the answer it is testing always passes.
    raw = subprocess.run([sys.executable, os.path.join(CLI.HERE, "cursor_cli.py"),
                          "status"], capture_output=True, text=True)
    line = [x for x in raw.stdout.splitlines() if x.strip()][-1]
    published = [v["id"] for v in json.loads(line)["variants"]]
    checks.append(("status publishes every set, in order: %s" % ", ".join(published),
                   published == sets))

    # 4. Walk the gesture for real: install, ask for next, install, repeat.
    #    One hop proves nothing - a cycle stuck between two sets looks correct
    #    until you press it a third time.
    # Whatever the user had on before this test runs. Walking the cycle
    # installs three sets, so without this the test quietly leaves the machine
    # on sets[0] - which is exactly how the pointer kept reverting to RETICLE
    # after every update run.
    was = {k: v["value"] for k, v in I.read_current().items()}.get("Arrow", "")
    parts = was.replace("/", "\\").split("\\")
    was = parts[parts.index("cursors") + 1] if "cursors" in parts else None

    start = sets[0]
    seen = []
    with contextlib.redirect_stdout(io.StringIO()):     # install() is chatty
        I.install(start)
        for _ in range(len(sets)):
            cfg = {k: v["value"] for k, v in I.read_current().items()}
            nxt = CE.next_variant(cfg.get("Arrow", ""))
            I.install(nxt)
            seen.append(nxt)
    checks.append(("gesture visits all %d sets: %s" % (len(sets), " -> ".join(seen)),
                   sorted(seen) == sorted(sets)))
    checks.append(("gesture wraps back to where it started", seen[-1] == start))

    if was in sets:
        with contextlib.redirect_stdout(io.StringIO()):
            I.install(was)
    checks.append(("puts the pointer back afterwards: %s" % (was or "(untouched)"),
                   was not in sets or installed_now() == was))

    # 5. A settings file naming a set that no longer exists must heal, not kill.
    saved = None
    if os.path.isfile(CLI.SETTINGS):
        saved = open(CLI.SETTINGS, encoding="utf-8").read()
    try:
        CLI.save_settings(dict(CLI.DEFAULTS, variant="a_set_that_was_deleted"))
        healed = CLI.load_settings()["variant"]
        checks.append(("a deleted set name heals to %s" % healed, healed in sets))
    finally:
        if saved is not None:
            open(CLI.SETTINGS, "w", encoding="utf-8").write(saved)

    bad = 0
    for label, ok in checks:
        print("  %-58s %s" % (label, "PASS" if ok else "FAIL"))
        bad += 0 if ok else 1
    if bad:
        sys.exit("%d set check(s) failed" % bad)
    print("every set is reachable, named and cycled")


if __name__ == "__main__":
    main()
