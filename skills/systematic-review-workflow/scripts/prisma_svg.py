"""Shared PRISMA 2020 flow-diagram SVG generator ("neo" style).

Imported by both srw_cli.py (report step) and gen_portal.py (portal step)
so the diagram is available as early as step 7 and is always consistent.
"""

def prisma_svg(n_identified, n_dedup, n_screened, n_abs_exc, n_ft_screen, n_ft_exc, n_included):
    """Return a self-contained PRISMA 2020 flow diagram SVG string.

    Style: lavender #ECECFF nodes, purple #9370DB strokes, feDropShadow,
    arrow markers, Trebuchet MS font via CSS class .lbl.
    Any count argument may be None → rendered as "—".
    """
    # ---- node layout ----------------------------------------------------
    nodes = []
    def _n(cx, cy, l1, l2, final=False):
        nodes.append({"cx": cx, "cy": cy, "l1": l1, "l2": l2, "final": final})

    def _cnt(v):
        return "n=" + str(v) if v is not None else "—"

    _n(184,  60, "Records identified",        _cnt(n_identified), False)
    _n(184, 196, "After duplicate removal",   _cnt(n_dedup),      False)
    _n(184, 332, "Screened at title/abstract", _cnt(n_screened),   False)
    _n(88,  468, "Excluded at abstract",      _cnt(n_abs_exc),    False)
    _n(280, 468, "Full-text screened",        _cnt(n_ft_screen),  False)
    _n(184, 604, "Excluded at full-text",     _cnt(n_ft_exc),     False)
    _n(376, 604, "Included in review",        _cnt(n_included),   True)

    NW, NH = 152, 96
    HW, HH = NW / 2, NH / 2
    fill   = "#ECECFF"
    stroke = "#9370DB"
    edge_c = "#333333"

    parts = []
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="4 4 456 656" '
        'style="max-width:456px;width:100%;height:auto;display:block;margin:0 auto;" '
        'role="img" aria-label="PRISMA 2020 flow diagram">')

    # CSS class (avoids attribute-quoting issues with comma-separated fonts)
    parts.append(
        '<style>.lbl{font-family:"trebuchet ms",verdana,arial,sans-serif;'
        'font-size:13px;fill:#333;}</style>')

    # defs: arrow marker + drop shadow
    parts.append(
        '<defs>'
        '<marker id="arr" viewBox="0 0 11.5 14" refX="11.5" refY="7" '
        'markerUnits="userSpaceOnUse" markerWidth="10.5" markerHeight="14" orient="auto">'
        f'<path d="M 0 0 L 11.5 7 L 0 14 z" fill="{edge_c}"/>'
        '</marker>'
        '<filter id="ds"><feDropShadow dx="1" dy="2" stdDeviation="2" '
        'flood-opacity="0.3" flood-color="#b9b9b9"/></filter>'
        '</defs>')

    # ---- edge helpers ---------------------------------------------------
    def edge(x1, y1, x2, y2):
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{edge_c}" stroke-width="1.5" marker-end="url(#arr)"/>')

    def edge_curve(x1, y1, x2, y2):
        mid_y = (y1 + y2) / 2
        dx = 6 if x2 > x1 else -6
        parts.append(
            f'<path d="M{x1},{y1}L{x1},{mid_y - 4}'
            f'Q{x1},{mid_y} {x1 + dx},{mid_y} '
            f'L{x2 - dx},{mid_y}Q{x2},{mid_y} {x2},{mid_y + 4}L{x2},{y2}" '
            f'stroke="{edge_c}" stroke-width="1.5" fill="none" marker-end="url(#arr)"/>')

    # ---- edges ----------------------------------------------------------
    edge(184, 60 + HH,   184, 196 - HH)   # A→B
    edge(184, 196 + HH,  184, 332 - HH)   # B→C
    edge_curve(162, 332 + HH, 88, 468 - HH)   # C→D (left split)
    edge_curve(206, 332 + HH, 280, 468 - HH)  # C→E (right split)
    edge_curve(258, 468 + HH, 184, 604 - HH)  # E→F (left split)
    edge_curve(302, 468 + HH, 376, 604 - HH)  # E→G (right split)

    # ---- nodes ----------------------------------------------------------
    for nd in nodes:
        cx, cy = nd["cx"], nd["cy"]
        rx, ry = cx - HW, cy - HH
        parts.append(
            f'<rect x="{rx}" y="{ry}" width="{NW}" height="{NH}" rx="4" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" filter="url(#ds)"/>')
        parts.append(
            f'<text x="{cx}" y="{cy - 6}"  text-anchor="middle" class="lbl">{nd["l1"]}</text>')
        parts.append(
            f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" class="lbl">{nd["l2"]}</text>')

    parts.append('</svg>')
    return "".join(parts)


# ---- convenience: normalise counts that may arrive as lists --------------
def normalise_ft_scr(val):
    """Return an int count.  Accepts int, list (of indices), or None."""
    if val is None:
        return None
    if isinstance(val, list):
        return len(val)
    return int(val)
