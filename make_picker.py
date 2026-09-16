"""Emit a selectable card grid of all 30 cursors, straight from the real geometry.

The widget is generated rather than hand-written so the shapes on the picker are
provably the same polygons that go into the .cur files - no second source of
truth to drift.

    python make_picker.py keep     which roles to install
    python make_picker.py remove   which roles to delete from disk
"""

import os
import sys

import build_cursors as B

HERE = os.path.dirname(os.path.abspath(__file__))
CUR = os.path.join(HERE, "cursors")

ROLES = B.ROLES_STATIC + ["Wait", "AppStarting"]

NOTE = {
    "Arrow": "normal select", "Hand": "links, buttons", "IBeam": "text fields",
    "Crosshair": "precision select", "No": "not allowed", "Help": "what is this",
    "UpArrow": "alternate select", "SizeNS": "drag top / bottom edge",
    "SizeWE": "drag left / right edge", "SizeNWSE": "drag corner",
    "SizeNESW": "drag corner", "SizeAll": "move window", "NWPen": "handwriting",
    "Wait": "busy, spins", "AppStarting": "loading, spins",
}


def role_kb(variant, role):
    """Disk cost of a role: its resting file plus its pressed twin."""
    ext = ".ani" if role in ("Wait", "AppStarting") else ".cur"
    total = 0
    for state in ("resting", "pressed"):
        p = os.path.join(CUR, variant, state, role + ext)
        if os.path.isfile(p):
            total += os.path.getsize(p)
    return total / 1024.0


def pts(poly):
    return " ".join("%g,%g" % (round(x, 1), round(y, 1)) for x, y in poly)


def polys(shapes, fill=None):
    """shapes is [poly, ...] or [(poly, colour), ...]."""
    out = []
    for item in shapes:
        poly, col = (item, fill) if fill else item
        c = {"cyan": "#00d9ff", "ginger": "#ff7a18"}.get(col, col)
        out.append('<polygon class="s" points="%s" fill="%s"/>' % (pts(poly), c))
    return "".join(out)


def defs():
    d = ['<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>']
    d.append('<g id="rf">%s</g>' % polys(B.OUTER, "ginger"))
    d.append('<g id="rft">%s</g>' % polys(B.OUTER_TIGHT, "ginger"))
    for ident, base in (("of", B.OUTLINE), ("oft", B.OUTLINE_TIGHT)):
        frame = ([(p, "cyan") for p in base] +
                 [(B.rotate(p, 45), "ginger") for p in base])
        d.append('<g id="%s">%s</g>' % (ident, polys(frame)))
    for role in ROLES:
        body = polys(B.centre_glyph(role), "currentColor")
        if role == "Help":
            # The real Help cursor draws its "?" with PIL, not as a polygon, so
            # without this the card would show a bare frame and read as Arrow.
            body += ('<text x="64" y="65" text-anchor="middle"'
                     ' dominant-baseline="central" font-size="40"'
                     ' font-family="Times New Roman, serif" fill="currentColor"'
                     ' stroke="#08131f" stroke-width="3"'
                     ' paint-order="stroke">?</text>')
        d.append('<g id="g%s">%s</g>' % (role, body))
    d.append("</defs></svg>")
    return "".join(d)


def card(n, variant, role, mode):
    # Hand closes its frame inward in BOTH variants - that is the only thing
    # telling it apart from Arrow now that the centre is empty.
    tight = role == "Hand"
    frame = ("rft" if tight else "rf") if variant == "reticle" else \
            ("oft" if tight else "of")
    colour = "#00d9ff" if variant == "reticle" else "#ff7a18"
    kb = role_kb(variant, role)
    gone = kb == 0
    cls = "c gone" if gone else "c" if mode == "remove" else "c on"
    svg = ('<svg viewBox="0 0 128 128" width="74" height="74">'
           '<use href="#%s"/><g style="color:%s"><use href="#g%s"/></g></svg>'
           % (frame, colour, role))
    tag = "ALREADY GONE" if gone else ("KEEP" if mode == "keep" else "")
    size = "-" if gone else "%d KB" % round(kb)
    return ('<button class="%s" data-k="%s/%s" data-kb="%.0f" onclick="t(this)">'
            '<span class="n">%d</span><span class="kb">%s</span>%s'
            '<span class="r">%s</span><span class="d">%s</span>'
            '<span class="chk">%s</span></button>'
            % (cls, variant, role, kb, n, size, svg, role, NOTE[role], tag))


CSS = """<style>
 .w{font-family:var(--font-sans)}
 .sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
 .hd{font-family:var(--font-voice),'Times New Roman',serif;font-size:15px;
     color:var(--text-accent);letter-spacing:.06em;margin:16px 0 8px}
 .g{display:grid;grid-template-columns:repeat(8,1fr);gap:6px}
 @media(max-width:1100px){.g{grid-template-columns:repeat(5,1fr)}}
 @media(max-width:700px){.g{grid-template-columns:repeat(3,1fr)}}
 @media(max-width:430px){.g{grid-template-columns:repeat(2,1fr)}}
 .c{position:relative;border:1px solid var(--border-strong);border-radius:0;
    background:var(--surface-1);padding:16px 4px 8px;display:flex;
    flex-direction:column;align-items:center;cursor:pointer;min-height:162px;
    font:inherit;transition:opacity .1s,border-color .1s,background .1s}
 .c .s{stroke:#08131f;stroke-width:3;stroke-linejoin:round}
 .c:hover{border-color:var(--border-accent)}
 .c.rm{border-color:var(--border-danger);background:var(--bg-danger);opacity:.55}
 .c.rm svg{filter:grayscale(1)}
 .c.gone{opacity:.2;cursor:not-allowed;border-style:dashed}
 .n{position:absolute;top:0;left:0;background:#111;color:#d4af37;
    font-family:var(--font-voice),'Times New Roman',serif;font-size:12px;
    line-height:1;padding:4px 6px}
 .kb{position:absolute;top:0;right:0;background:var(--surface-2);
     color:var(--text-secondary);font-size:9px;padding:4px 5px;letter-spacing:.04em}
 .chk{position:absolute;bottom:0;right:0;left:0;background:#0d5c2f;color:#d6ffe6;
      font-size:9px;letter-spacing:.08em;padding:3px 0;text-align:center}
 .chk:empty{display:none}
 .c.rm .chk{display:block;background:#7a1f1f;color:#ffd6d6}
 .c.rm .chk:after{content:"DELETE"}
 .c.rm .chk{font-size:0}
 .c.rm .chk:after{font-size:9px}
 .r{font-family:var(--font-voice),'Times New Roman',serif;font-size:13px;
    color:var(--text-primary);margin-top:6px}
 .d{font-size:10px;color:var(--text-secondary);text-align:center;line-height:1.3;
    margin-top:2px}
 .bar{position:sticky;bottom:0;display:flex;gap:8px;align-items:center;
      flex-wrap:wrap;margin-top:14px;padding:10px;border:1px solid var(--border-strong);
      background:var(--surface-2)}
 .cnt{font-family:var(--font-voice),'Times New Roman',serif;font-size:14px;
      color:var(--text-primary);margin-right:auto}
 .cnt b{color:#d4af37}
 .b{border:1px solid var(--border-strong);border-radius:0;background:var(--surface-1);
    color:var(--text-primary);font:inherit;font-size:12px;padding:7px 12px;cursor:pointer}
 .b:hover{border-color:var(--border-accent)}
 .b.go{background:#7a1f1f;color:#ffe9e9;border-color:#7a1f1f}
 .b.keep{background:#0d5c2f;color:#eafff2;border-color:#0d5c2f}
</style>"""

SCRIPT_REMOVE = """<script>
function all(){return [].slice.call(document.querySelectorAll('.c'))
  .filter(function(c){return !c.classList.contains('gone')})}
function t(el){if(el.classList.contains('gone'))return;el.classList.toggle('rm');u()}
function set(pfx,on){all().forEach(function(c){
  if(!pfx||c.dataset.k.indexOf(pfx)===0)c.classList.toggle('rm',on)});u()}
function u(){
  var m=all().filter(function(c){return c.classList.contains('rm')});
  var kb=m.reduce(function(a,c){return a+parseFloat(c.dataset.kb||0)},0);
  document.getElementById('cnt').innerHTML='<b>'+m.length+'</b> of '+all().length+
    ' marked to delete &nbsp;.&nbsp; frees <b>'+(kb/1024).toFixed(2)+'</b> MB';
}
function go(){
  var m=all().filter(function(c){return c.classList.contains('rm')})
             .map(function(c){return c.dataset.k});
  if(!m.length){alert('Nothing marked.');return;}
  sendPrompt('Delete exactly these cursor roles, both their resting and pressed '+
    'files, and nothing else. List what you will delete and wait for my yes '+
    'first.\\n\\nDELETE ('+m.length+'): '+m.join(', '));
}
u();
</script>"""


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "remove").lower()
    out = os.path.join(HERE, "picker_%s.html" % mode)

    heads = {
        "remove": ("Every cursor role still on disk, as toggle cards. Nothing is "
                   "marked; click a card to mark it for deletion.",
                   "CLICK WHAT TO DELETE"),
        "keep": ("All thirty cursors as toggle cards. Every card starts kept; "
                 "click one to drop it.", "CLICK WHAT TO KEEP"),
    }
    sr, banner = heads.get(mode, heads["remove"])

    html = ['<h2 class="sr-only">%s</h2>' % sr, CSS, defs(), '<div class="w">']
    html.append('<div class="hd">%s</div>' % banner)

    n = 0
    for variant, title in (
            ("reticle", "A .  RETICLE  &nbsp;&mdash;&nbsp; ginger frame, blue centre"),
            ("octagram", "B .  8 STAR  (RUB EL HIZB)  &nbsp;&mdash;&nbsp; outline, interlaces on click")):
        html.append('<div class="hd">%s</div><div class="g">' % title)
        for role in ROLES:
            n += 1
            html.append(card(n, variant, role, mode))
        html.append("</div>")

    html.append(
        '<div class="bar"><span class="cnt" id="cnt"></span>'
        '<button class="b keep" onclick="set(\'\',false)">clear all marks</button>'
        '<button class="b" onclick="set(\'reticle\',true)">delete all Reticle</button>'
        '<button class="b" onclick="set(\'octagram\',true)">delete all 8 Star</button>'
        '<button class="b go" onclick="go()">Delete what I marked</button></div>')
    html.append("</div>")
    html.append(SCRIPT_REMOVE)

    open(out, "w", encoding="utf-8").write("".join(html))
    print("%s  (%.1f KB)" % (out, os.path.getsize(out) / 1024.0))


if __name__ == "__main__":
    main()
