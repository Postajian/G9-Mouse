"""Print the cursor set Windows is currently pointing at, and nothing else.

Small on purpose: every test and script that needs this was re-deriving it from
the registry path inline, and two of them got the backslash handling wrong.

    python which_set.py
"""

import install_cursors as I


def installed_set():
    cfg = {k: v["value"] for k, v in I.read_current().items()}
    parts = cfg.get("Arrow", "").replace("/", "\\").split("\\")
    return parts[parts.index("cursors") + 1] if "cursors" in parts else "windows"


if __name__ == "__main__":
    print(installed_set())
