"""
Build a pixel-perfect static replica of the live HF homepage.

Inputs (already in this folder):
  - page-source.html  (curl'd SSR shell from LIVE_BASE)
  - style.css         (curl'd compiled CSS)

Outputs:
  - index.html        (replica entry — open in a browser / serve statically)
  - style.css         (rewritten in place: relative url(...) -> absolute)

Procedure (per .claude live-page-replica skill):
  1. The HF homepage is server-rendered: <main> innerHTML in SSR matches the
     hydrated DOM within ~1KB. So we can build the replica straight off the
     SSR HTML without a Blob export from the browser.
  2. Pin <html class="dark"> so the compiled CSS's dark-mode rules apply.
  3. Point the stylesheet link at our local style.css.
  4. Strip framework JS so the page is a static replica (no hydration, no
     attempts to reach the moon-landing backend).
  5. Absolutize every '/foo' src/href/srcset so assets resolve against the
     live origin.
  6. Strip SvelteKit comment markers (<!--[-->, <!--]-->, <!--hash-->).
  7. Rewrite relative url(...) inside the compiled CSS the same way.

Re-running this script is idempotent.
"""
from __future__ import annotations

import re
from pathlib import Path

LIVE_BASE = "https://new-ui-homepage-ff.us.dev.moon.huggingface.tech"
PROJECT = Path(__file__).parent

SOURCE_HTML = PROJECT / "page-source.html"
SOURCE_CSS = PROJECT / "style.css"
OUT_HTML = PROJECT / "index.html"
OUT_CSS = SOURCE_CSS  # rewritten in place

# Track path along which the soft-blurred reveal mask travels.
# The middle two cubic Bezier segments match the central S-curve from
# dashed-dark.svg / filled-dark.svg exactly. The straight L segments on
# either end carry the mask off-canvas — at the start it slides in from
# the left (the blur falloff fades it in subtly), and at the end it
# continues past the right edge until nothing of the filled state is
# visible. Then the loop restarts.
INFERENCE_TRACK = (
    "M -80 169.54 "
    "L  80.59 169.54 "
    "C 145.32 288.37, 338.25 293.00, 413.40 169.54 "
    "C 488.55  46.07, 682.92  50.75, 748.66 169.54 "
    "L 920 169.54"
)
INFERENCE_DURATION = "10s"


def absolutize_attr(html: str) -> str:
    """src="/foo" / href="/foo" -> src/href="https://live/foo"."""

    def repl(m: re.Match) -> str:
        attr = m.group(1)
        quote = m.group(2)
        val = m.group(3)
        if val.startswith(("http://", "https://", "//", "#", "data:", "mailto:", "tel:", "javascript:")):
            return m.group(0)
        if val.startswith("/"):
            return f'{attr}={quote}{LIVE_BASE}{val}{quote}'
        return m.group(0)

    return re.sub(r'\b(src|href)=(["\'])([^"\']+)\2', repl, html)


def absolutize_srcset(html: str) -> str:
    """srcset="/a 1x, /b 2x" -> srcset="https://live/a 1x, https://live/b 2x"."""

    def fix_one(candidate: str) -> str:
        candidate = candidate.strip()
        if not candidate:
            return candidate
        parts = candidate.split(maxsplit=1)
        url = parts[0]
        rest = (" " + parts[1]) if len(parts) > 1 else ""
        if url.startswith(("http://", "https://", "//", "data:")):
            return candidate
        if url.startswith("/"):
            return f"{LIVE_BASE}{url}{rest}"
        return candidate

    def repl(m: re.Match) -> str:
        quote = m.group(1)
        candidates = m.group(2).split(",")
        rebuilt = ", ".join(fix_one(c) for c in candidates)
        return f'srcset={quote}{rebuilt}{quote}'

    return re.sub(r'srcset=(["\'])([^"\']+)\1', repl, html)


def absolutize_css_urls(css: str) -> str:
    """url(/foo) inside CSS -> url(https://live/foo)."""

    def repl(m: re.Match) -> str:
        quote = m.group(1) or ""
        val = m.group(2)
        if val.startswith(("http://", "https://", "//", "data:")):
            return m.group(0)
        if val.startswith("/"):
            return f"url({quote}{LIVE_BASE}{val}{quote})"
        return m.group(0)

    return re.sub(r"url\(([\'\"]?)([^\'\")]+)\1\)", repl, css)


def strip_framework_comments(html: str) -> str:
    """SvelteKit emits noisy comment markers. They're not needed for a static
    replica — strip them so the markup reads cleanly without changing layout."""
    html = re.sub(r"<!--\[-->", "", html)
    html = re.sub(r"<!--\]-->", "", html)
    html = re.sub(r"<!---->", "", html)
    # 4-12 char alphanumeric IDs (SvelteKit's per-hydration markers).
    html = re.sub(r"<!--[a-z0-9]{4,12}-->", "", html)
    return html


def strip_scripts(html: str) -> str:
    """Remove <script>...</script> so the static replica doesn't try to
    hydrate or call the moon-landing backend."""
    return re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)


def _svg_inner(svg_text: str) -> str:
    """Return the markup between the outermost <svg ...> and </svg>."""
    m = re.search(r"<svg\b[^>]*>(.*)</svg>\s*$", svg_text, flags=re.DOTALL)
    if not m:
        raise ValueError("SVG file missing <svg>...</svg> wrapper")
    return m.group(1)


def build_inference_graphic() -> str:
    """Combine dashed-dark.svg + filled-dark.svg into one inline SVG where the
    filled layer is revealed by a soft-blurred circle that travels along the
    central track via SMIL <animateMotion>. Pure SVG — no JS, no CSS keyframes,
    so it hot-reloads cleanly and the animation can't drift."""

    dashed = (PROJECT / "dashed-dark.svg").read_text(encoding="utf-8")
    filled = (PROJECT / "filled-dark.svg").read_text(encoding="utf-8")

    dashed_inner = _svg_inner(dashed)
    filled_inner = _svg_inner(filled)

    # The filled SVG carries its own <defs> (radial gradients + a luminance
    # mask for the HF emoji). Hoist it into the combined <defs> so refs still
    # resolve — and strip it from the body to avoid duplicate definitions.
    defs_m = re.search(r"<defs\b[^>]*>(.*?)</defs>", filled_inner, flags=re.DOTALL)
    filled_defs = defs_m.group(1) if defs_m else ""
    filled_body = re.sub(r"<defs\b[^>]*>.*?</defs>", "", filled_inner, flags=re.DOTALL)

    # Inline style instead of Tailwind classes — moon-landing's compiled CSS
    # doesn't include the arbitrary utilities we'd need (w-[848px] etc.), and
    # the replica is a static build with no Tailwind processor.
    return (
        '<svg viewBox="0 0 848 339" fill="none" '
        'preserveAspectRatio="xMidYMid meet" aria-hidden="true" '
        'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'style="position:absolute;top:calc(50% - 15px);left:calc(50% - 10px);width:144%;max-width:950px;height:auto;'
        'transform:translate(-50%,-46%);pointer-events:none">'
        '<defs>'
        '<filter id="ig-blur" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur stdDeviation="32"/>'
        '</filter>'
        '<mask id="ig-reveal" maskUnits="userSpaceOnUse" x="0" y="0" width="848" height="339">'
        '<rect width="848" height="339" fill="black"/>'
        '<circle r="70" fill="white" filter="url(#ig-blur)">'
        f'<animateMotion dur="{INFERENCE_DURATION}" repeatCount="indefinite" rotate="0" path="{INFERENCE_TRACK}"/>'
        '</circle>'
        '</mask>'
        f'{filled_defs}'
        '</defs>'
        f'<g>{dashed_inner}</g>'
        f'<g mask="url(#ig-reveal)">{filled_body}</g>'
        '</svg>'
    )


def rearrange_enterprise_section(html: str) -> str:
    """Make the Enterprise section's left content (heading + body + button +
    caption) span the full article width, extract the 6 feature boxes from
    the right column, and re-insert them as a new full-width section just
    after the sliding team/org ribbon. The new boxes layout is 3 columns × 2
    rows at lg+ (was 2 cols × 3 rows in the half-width column)."""

    GRID_OPEN = (
        '<div class="grid w-full divide-gray-200/70 dark:divide-gray-850'
        ' max-lg:divide-y lg:grid-cols-2 lg:divide-x">'
    )
    grid_start = html.find(GRID_OPEN)
    if grid_start == -1:
        return html
    content_start = grid_start + len(GRID_OPEN)

    # Find matching </div> for the enterprise 2-col grid.
    depth = 1
    pos = content_start
    while pos < len(html) and depth > 0:
        no = html.find("<div", pos)
        nc = html.find("</div>", pos)
        if nc == -1:
            return html
        if no != -1 and no < nc:
            depth += 1
            pos = no + 4
        else:
            depth -= 1
            pos = nc + 6
    grid_close_idx = pos - 6
    grid_end_after = pos
    grid_content = html[content_start:grid_close_idx]

    # Extract the two columns (direct child divs).
    cols = []
    p = 0
    while True:
        op = grid_content.find("<div", p)
        if op == -1:
            break
        d = 1
        i = op + 4
        while i < len(grid_content) and d > 0:
            no = grid_content.find("<div", i)
            nc = grid_content.find("</div>", i)
            if nc == -1:
                break
            if no != -1 and no < nc:
                d += 1
                i = no + 4
            else:
                d -= 1
                i = nc + 6
        cols.append(grid_content[op:i])
        p = i
    if len(cols) != 2:
        return html

    left_col_html, right_col_html = cols

    # Extract the 6-box grid from the right column.
    BOX_GRID_OPEN = (
        '<div class="grid grid-cols-2 gap-px bg-gray-200/70 dark:bg-gray-850'
        ' sm:grid-cols-3 lg:grid-cols-2">'
    )
    box_start = right_col_html.find(BOX_GRID_OPEN)
    if box_start == -1:
        return html
    bp = box_start + len(BOX_GRID_OPEN)
    d = 1
    while bp < len(right_col_html) and d > 0:
        no = right_col_html.find("<div", bp)
        nc = right_col_html.find("</div>", bp)
        if nc == -1:
            break
        if no != -1 and no < nc:
            d += 1
            bp = no + 4
        else:
            d -= 1
            bp = nc + 6
    box_grid_full = right_col_html[box_start:bp]

    # Switch from 2 cols (half-width column) to 3 cols (full-width row of 6 → 2 rows × 3).
    # Add auto-rows-fr so both rows match in height regardless of description length.
    new_box_grid = box_grid_full.replace(
        "grid-cols-2 gap-px bg-gray-200/70 dark:bg-gray-850 sm:grid-cols-3 lg:grid-cols-2",
        "grid-cols-2 auto-rows-fr gap-px bg-gray-200/70 dark:bg-gray-850 sm:grid-cols-3 lg:grid-cols-3",
        1,
    )

    # Reshape each box from a vertical flex column (icon on top of text) to
    # a 2-col grid with the icon on the left spanning both rows of text
    # (title above description). moon-landing's compiled Tailwind doesn't
    # include the arbitrary `grid-cols-[auto_1fr]` utility, so we pin the
    # template via an inline style attribute on each box.
    new_box_grid = new_box_grid.replace(
        "group/item relative z-1 flex flex-col items-start bg-white p-4 text-left sm:p-5",
        'group/item relative z-1 grid items-start gap-x-3'
        ' bg-white p-4 text-left sm:p-5"'
        ' style="grid-template-columns: auto 1fr',
    )
    # Icon: drop mb-2 (no longer needed since grid handles spacing) and span
    # both rows via inline style. The arbitrary `row-span-2` utility isn't
    # in moon-landing's compiled CSS, so we set grid-row directly.
    new_box_grid = new_box_grid.replace(
        'mb-2 flex size-9 items-center justify-center rounded-xl bg-gray-100 text-gray-500 dark:bg-gray-850 dark:text-gray-400"',
        'flex size-9 items-center justify-center rounded-xl bg-gray-100 text-gray-500 dark:bg-gray-850 dark:text-gray-400" style="grid-row: span 2"',
    )
    new_box_grid = new_box_grid.replace(
        '<p class="mt-1 text-balance text-sm leading-snug',
        '<p class="text-balance text-sm leading-snug',
    )

    # Wrap the box grid in the same hatched-strip frame the live SSR uses
    # around the enterprise boxes. The strips have:
    #   - LEFT vertical strip (16-20px wide) with hatched bg + dashed right
    #     border, plus two small white corner caps so the dashed top/bottom
    #     strip borders terminate cleanly.
    #   - TOP horizontal strip (16-20px tall) with hatched bg + dashed bottom
    #     border.
    #   - BOTTOM horizontal strip with hatched bg + dashed top border.
    #   - RIGHT vertical strip mirroring the left.
    # This is the same markup used on https://new-ui-homepage-ff.us.dev/.
    # Note: `h-full` is intentionally OMITTED here even though the live SSR
    # uses it. The live page nests the strip inside a 2-col grid row that
    # has an auto-stretched height — so `height: 100%` resolves. Our boxes
    # section is its own standalone section, so the strip's parent height
    # is auto, and `h-full` evaluates to auto and stops the default flex
    # `align-items: stretch` from stretching the strip to match the middle
    # column's height. Removing it lets stretch kick in cleanly.
    LEFT_STRIP = (
        '<div class="relative z-1 flex w-4 min-w-4 shrink flex-col'
        ' justify-between border-r border-dashed border-gray-200 dark:border-gray-850'
        ' lg:w-5 lg:min-w-5">'
        '<div class="size-4 border-b border-r border-dashed border-gray-200'
        ' dark:border-gray-850 bg-white dark:bg-gray-950 lg:size-5"></div>'
        '<div class="size-4 border-r border-t border-dashed border-gray-200'
        ' dark:border-gray-850 bg-white dark:bg-gray-950 lg:size-5"></div>'
        '<div class="hatched-pattern absolute inset-0 -z-1"></div>'
        '</div>'
    )
    RIGHT_STRIP = (
        '<div class="relative z-1 flex w-4 min-w-4 shrink flex-col'
        ' justify-between border-l border-dashed border-gray-200 dark:border-gray-850'
        ' lg:w-5 lg:min-w-5">'
        '<div class="size-4 border-b border-l border-dashed border-gray-200'
        ' dark:border-gray-850 bg-white dark:bg-gray-950 lg:size-5"></div>'
        '<div class="size-4 border-l border-t border-dashed border-gray-200'
        ' dark:border-gray-850 bg-white dark:bg-gray-950 lg:size-5"></div>'
        '<div class="hatched-pattern absolute inset-0 -z-1"></div>'
        '</div>'
    )
    TOP_STRIP = (
        '<div class="relative h-4 w-full border-b border-dashed border-gray-200'
        ' dark:border-gray-850 lg:h-5">'
        '<div class="hatched-pattern absolute inset-0 -z-1"></div>'
        '</div>'
    )
    BOTTOM_STRIP = (
        '<div class="relative h-4 w-full border-t border-dashed border-gray-200'
        ' dark:border-gray-850 lg:h-5">'
        '<div class="hatched-pattern absolute inset-0 -z-1"></div>'
        '</div>'
    )

    BOXES_SECTION = (
        '<section class="border-b border-gray-200/70 dark:border-gray-850 px-4">'
        '<article class="mx-auto max-w-7xl border-l border-r border-gray-200/70'
        ' dark:border-gray-850">'
        '<div class="relative z-1 flex">'
        + LEFT_STRIP
        + '<div class="flex flex-1 flex-col">'
        + TOP_STRIP
        + new_box_grid
        + BOTTOM_STRIP
        + '</div>'
        + RIGHT_STRIP
        + '</div>'
        + "</article>"
        "</section>"
    )

    # Replace the enterprise 2-col grid with just the left content (no grid wrapper).
    html = html[:grid_start] + left_col_html + html[grid_end_after:]

    # Insert the boxes section right after the sliding team/org ribbon's <section>.
    slide_idx = html.find("ribbon-animation__slideshow")
    if slide_idx == -1:
        # No ribbon found; append right after the enterprise spot as a fallback.
        return (
            html[: grid_start + len(left_col_html)]
            + BOXES_SECTION
            + html[grid_start + len(left_col_html):]
        )
    section_start = html.rfind("<section", 0, slide_idx)
    if section_start == -1:
        return html
    # Walk forward to find matching </section>.
    d = 1
    sp = section_start + 8
    while sp < len(html) and d > 0:
        no = html.find("<section", sp)
        nc = html.find("</section>", sp)
        if nc == -1:
            break
        if no != -1 and no < nc:
            d += 1
            sp = no + 8
        else:
            d -= 1
            sp = nc + 10
    return html[:sp] + BOXES_SECTION + html[sp:]


def remove_compute_hardware_boxes(html: str) -> str:
    """Strip the 4 hardware pricing cards from the Compute section.
    Finds the absolutely-positioned 2-col grid that holds them and removes
    the entire grid (open tag through its matching </div>) so the section
    keeps its layout but the cards are gone."""

    GRID_OPEN = (
        '<div class="absolute left-1/2 top-0 z-1 grid w-full'
        ' -translate-x-1/2 grid-cols-2 gap-4 px-6 sm:px-10 lg:px-14">'
    )
    start = html.find(GRID_OPEN)
    if start == -1:
        return html

    # Walk forward, balancing <div>/</div>, to find the matching close.
    depth = 1
    pos = start + len(GRID_OPEN)
    while pos < len(html) and depth > 0:
        nxt_open = html.find("<div", pos)
        nxt_close = html.find("</div>", pos)
        if nxt_close == -1:
            return html
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            pos = nxt_close + 6
    return html[:start] + html[pos:]


def add_compute_graphic(html: str) -> str:
    """Place the Compute illustration into the empty graphic panel that sits
    below the Compute heading, and overlay a radial gradient that fades the
    graphic into the page background.

    Positioning follows Figma frame `362:50285` ("Article"):
      - "compute graphic" container (640 × 567) wraps both heading and graphic.
      - Inner "Compute Graphic" SVG: 1121.56 × 648.7 at (-289, 311.83) — i.e.
        ~175% of container width, overflowing the left edge by ~45% and
        starting 55% down the column (right where the heading ends).

    Our SSR splits that 640 × 567 column into a heading block on top and a
    `min-h-38 flex-1 overflow-hidden` panel below. The panel only covers the
    bottom ~45% of the original Figma column, so the inner-SVG's vertical
    offset translates to 0 (pin to the top of the panel). Horizontal scaling
    stays 175% / left-45% because the panel inherits the column's width.

    The gradient overlay matches Figma's "Rectangle 11" (380:1653): a wide
    radial gradient anchored at the bottom-right corner that is transparent
    inside 60.6% radius and opaque dark (rgb 11,15,25, matching Figma's
    paint server) at 100%. The ellipse axes are 99.3% × 67.8% of the
    overlay box, so most of the panel is darkened with a clear pocket in
    the bottom-right where the hardware cards peek through.
    """
    PANEL_OPEN = '<div class="min-h-38 relative w-full flex-1 overflow-hidden">'
    idx = html.find(PANEL_OPEN)
    if idx == -1:
        print("  ! couldn't find compute graphic panel — skipping graphic placement")
        return html

    # Bump the panel's minimum height so the visible slice of the SVG
    # matches Figma's 256px graphic area instead of the default 152px.
    # Tailwind's compiled CSS doesn't have a `min-h-64` utility variant we
    # can rely on, so set min-height via an inline style attribute.
    PANEL_OPEN_NEW = (
        '<div class="min-h-38 relative w-full flex-1 overflow-hidden"'
        ' style="min-height:256px">'
    )
    html = html.replace(PANEL_OPEN, PANEL_OPEN_NEW, 1)
    insert_at = html.find(PANEL_OPEN_NEW) + len(PANEL_OPEN_NEW)

    GRAPHIC = (
        # The SVG asset itself. URL-encode the space in the filename so the
        # browser doesn't trip on it. Tailwind's compiled CSS doesn't include
        # the arbitrary percentage utilities we need, so positioning rides on
        # an inline style attribute.
        '<img src="./Compute%20Graphic.svg" alt="" aria-hidden="true"'
        ' style="position:absolute;'
        'left:-45.16%;'
        'top:0;'
        'width:175.24%;'
        'max-width:none;'
        'height:auto;'
        'pointer-events:none;'
        'user-select:none;'
        'z-index:0"/>'
        # Radial gradient overlay = Figma's Rectangle 11 fill. The original
        # 60.6%→100% stops put the dark ring right over the hardware-cards
        # row in our shorter panel and washed them out; widening the
        # transparent zone to 72% keeps the dark fade only on the top half
        # of the panel so the cards in the bottom-right read clearly.
        '<div style="position:absolute;inset:0;pointer-events:none;z-index:1;'
        'background:radial-gradient('
        'ellipse 99.3% 67.8% at 96.4% 100%,'
        ' rgba(11,15,25,0) 72%,'
        ' rgba(11,15,25,1) 100%)"></div>'
    )
    return html[:insert_at] + GRAPHIC + html[insert_at:]


def style_compute_section(html: str) -> str:
    """Three tweaks to the right-hand Compute column:

    1. Swap the purple linear gradient for a fine purple dot pattern that
       fades out toward the top, so the panel reads as textured rather
       than washed.
    2. Open up the hardware-card chrome — uniform p-4 padding, gap-3
       between name row and spec list, softer border that matches the
       article's own dividers (gray-200/70 light, gray-850 dark), and a
       slight type bump on the spec lines for readability.
    3. Card background goes from semi-transparent gray-900/70 (which
       picked up purple bleed from the gradient) to solid gray-950 so it
       matches the page background exactly — outlined cards instead of
       raised panels.
    """

    # (1) Purple gradient -> dotted texture with mask-image fade.
    # Two mask gradients composited with `intersect`: a vertical "light
    # source" (peak at 76% — the centre line of the first card row, fading
    # both upward toward the heading and downward toward the bottom edge),
    # plus side fades (centre dense, edges transparent).
    SIDE_FADE = "linear-gradient(to right, transparent 0%, rgb(0,0,0) 12%, rgb(0,0,0) 88%, transparent 100%)"
    VERT_GLOW = "linear-gradient(180deg, transparent 0%, rgb(0,0,0) 76%, transparent 100%)"
    DOTTED_STYLE = (
        "background-image: radial-gradient(circle, rgba(139, 92, 246, 0.17) 1.1px, transparent 1.4px);"
        "background-size: 9px 9px;"
        f"-webkit-mask-image: {VERT_GLOW}, {SIDE_FADE};"
        "-webkit-mask-composite: source-in;"
        f"mask-image: {VERT_GLOW}, {SIDE_FADE};"
        "mask-composite: intersect;"
    )
    html = re.sub(
        r'<div class="bg-linear-to-t from-purple-500/20[^"]*"></div>',
        f'<div class="pointer-events-none absolute bottom-0 left-0 -z-1 h-full w-full" style="{DOTTED_STYLE}"></div>',
        html,
    )

    # (2) + (3) Hardware-card chrome
    html = html.replace(
        'class="flex flex-col items-start gap-2 rounded-lg border border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-gray-900/70"',
        'class="flex flex-col items-start gap-3 rounded-lg border border-gray-200/70 bg-white p-4 dark:border-gray-850 dark:bg-gray-950"',
    )

    # Spec line text bump (xs -> sm) for legibility
    html = html.replace(
        'class="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400"',
        'class="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400"',
    )

    # Push the price pills to the right edge of each card. The compiled header
    # has `flex items-center justify-between` but no `w-full`, so it collapses
    # to content width and justify-between has nothing to space. Add w-full.
    html = html.replace(
        '<div class="flex items-center justify-between"><p class="text-sm font-semibold leading-tight text-gray-900 dark:text-white">',
        '<div class="flex w-full items-center justify-between"><p class="text-sm font-semibold leading-tight text-gray-900 dark:text-white">',
    )

    return html


def add_clipped_ghost_cards(html: str) -> str:
    """Add a clipping strip above cols 2 and 4 of the Open Source cards
    grid. Inside it, two ghost cards carry the same chrome as the live
    library cards (hatched pattern + top-left gradient + 4 corner markers)
    and are translated up so only the top edge peeks below the strip's
    bottom — i.e. the cards are clipped at the section's top edge while
    the section's height is unchanged."""

    GHOST_CARD_INNER = (
        # Diagonal hatched pattern across the card.
        '<div class="hatched-pattern pointer-events-none absolute inset-0 -z-1"></div>'
        # Soft top-left gradient fill (white in light, gray-950 in dark).
        '<div class="bg-linear-to-tl pointer-events-none absolute inset-0 -z-1'
        " from-white via-white/70 to-transparent dark:from-gray-950"
        ' dark:via-gray-950/70"></div>'
        # Four 10×10 corner ticks (top-left, top-right, bottom-left, bottom-right).
        '<div class="absolute left-0 top-0 h-[10px] w-[10px] -translate-x-px -translate-y-px'
        ' border-l border-t border-gray-300/70 dark:border-gray-800"></div>'
        '<div class="absolute right-0 top-0 h-[10px] w-[10px] -translate-y-px translate-x-px'
        ' border-r border-t border-gray-300/70 dark:border-gray-800"></div>'
        '<div class="absolute bottom-0 left-0 h-[10px] w-[10px] -translate-x-px translate-y-px'
        ' border-b border-l border-gray-300/70 dark:border-gray-800"></div>'
        '<div class="absolute bottom-0 right-0 h-[10px] w-[10px] translate-x-px translate-y-px'
        ' border-b border-r border-gray-300/70 dark:border-gray-800"></div>'
    )
    GHOST_CARD = (
        '<div class="group/library relative h-48 border border-dashed border-gray-200'
        ' bg-white/50 dark:border-gray-800 dark:bg-gray-900/50">'
        # No translate — the card sits at the strip top so the TOP 32px is
        # what shows through (gradient is most transparent at top-left, so
        # the card body + top corner markers + hatched pattern read clearly).
        # The strip's overflow-hidden clips the bottom 160px out.
        + GHOST_CARD_INNER
        + "</div>"
    )
    STRIP = (
        '<div class="pointer-events-none absolute inset-x-4 top-4 z-1 h-8'
        ' overflow-hidden md:inset-x-5 md:top-5">'
        # Mirror grid — same cols/gap as the main cards grid so the ghost
        # cards line up exactly with cols 2 and 4.
        '<div class="grid grid-cols-2 gap-4 md:grid-cols-4 md:gap-5">'
        "<div></div>" + GHOST_CARD + "<div></div>" + GHOST_CARD +
        "</div>"
        "</div>"
    )
    CARDS_WRAPPER_OPEN = (
        '<div class="relative border-t border-dashed border-gray-200 p-4 md:p-5">'
    )
    return html.replace(CARDS_WRAPPER_OPEN, CARDS_WRAPPER_OPEN + STRIP)


def add_oss_cards_to_cols_2_and_4(html: str) -> str:
    """Insert one empty library card at the top of columns 2 and 4 of the
    Our Open Source cards grid.

    The existing grid is `grid grid-cols-2 ... md:grid-cols-4 ...` with 12
    cards auto-flowed in row-major order. Naive insertion would scramble
    the columns (Diffusers would slide into col 3, etc.), so we:

    1. Walk the grid's direct-child cards by balanced div parsing.
    2. Annotate each existing card with explicit grid-column-start and
       grid-row-start matching its current visual position, but with
       cols 2 & 4 shifted down by one row.
    3. Inject two new empty cards at (col 2, row 1) and (col 4, row 1)
       — same h-48 / dashed border / semi-transparent bg / md:translate-y-8
       stagger as the existing offset cards, just empty.
    """
    GRID_OPEN = '<div class="grid grid-cols-2 gap-4 md:grid-cols-4 md:gap-5">'
    grid_start = html.find(GRID_OPEN)
    if grid_start == -1:
        return html
    content_start = grid_start + len(GRID_OPEN)

    # Find matching </div> via simple depth count.
    depth = 1
    pos = content_start
    while pos < len(html) and depth > 0:
        nxt_open = html.find("<div", pos)
        nxt_close = html.find("</div>", pos)
        if nxt_close == -1:
            return html
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            pos = nxt_close + 6
    grid_end = pos - 6
    grid_content = html[content_start:grid_end]

    # Extract each direct-child card. The cards are <a> elements (linkable)
    # with class="group/library ...". Find by class substring and walk
    # back to the tag opening, then balance the matching close tag.
    cards = []
    p = 0
    while True:
        cls_idx = grid_content.find('class="group/library', p)
        if cls_idx == -1:
            break
        tag_start = grid_content.rfind("<", 0, cls_idx)
        if tag_start == -1:
            break
        # Identify tag name (a or div etc).
        name_end = tag_start + 1
        while name_end < len(grid_content) and grid_content[name_end] not in " \t\n>":
            name_end += 1
        tag_name = grid_content[tag_start + 1 : name_end]
        open_end = grid_content.find(">", cls_idx)
        if open_end == -1:
            break
        close_tag = f"</{tag_name}>"
        open_tag_prefix = f"<{tag_name}"
        d = 1
        i = open_end + 1
        while i < len(grid_content) and d > 0:
            no = grid_content.find(open_tag_prefix, i)
            nc = grid_content.find(close_tag, i)
            if nc == -1:
                break
            if no != -1 and no < nc:
                d += 1
                i = no + len(open_tag_prefix)
            else:
                d -= 1
                i = nc + len(close_tag)
        cards.append(grid_content[tag_start:i])
        p = i

    if len(cards) != 12:
        # SSR shape changed — bail rather than corrupt the grid.
        return html

    # Annotate each card with explicit grid placement so the layout is
    # stable when we add cards at (col 2, row 1) / (col 4, row 1).
    updated_cards = []
    for idx, card in enumerate(cards):
        col = (idx % 4) + 1
        row = (idx // 4) + 1
        if col in (2, 4):
            row += 1  # make room for the new card at row 1
        style = f"grid-column-start:{col};grid-row-start:{row}"
        # Insert style attribute right after the opening tag name
        # (works whether the card is <a> or <div>).
        updated_cards.append(
            re.sub(
                r"(<[a-zA-Z]+\b)",
                rf'\1 style="{style}"',
                card,
                count=1,
            )
        )

    # New empty cards — same style/size/stagger as the offset cards.
    NEW_CARD_TPL = (
        '<div class="group/library h-48 border border-dashed border-gray-200'
        " bg-white/50 dark:border-gray-800 dark:bg-gray-900/50 md:translate-y-8\""
        ' style="grid-column-start:{col};grid-row-start:1"></div>'
    )
    new_2 = NEW_CARD_TPL.format(col=2)
    new_4 = NEW_CARD_TPL.format(col=4)

    new_grid = new_2 + new_4 + "".join(updated_cards)
    return html[:content_start] + new_grid + html[grid_end:]


def style_open_source_section(html: str) -> str:
    """Three tweaks to the Our Open Source section:

    1. Remove the bottom-half fade-to-page-bg div that hides the lower
       cards behind a soft fade.
    2. Replace the captured rotated-oval halo with the live page's
       current two corner halos — upper-left and lower-right
       half-circles tucked into the article corners (the SSR snapshot
       was older; the live page has been updated).
    3. Add two extra "ghost" blocks at the top of columns 2 and 4 via a
       parallel grid positioned at the article's top edge and translated
       up by half its height, so the upper half is sliced off by the
       article's overflow-hidden.
    """

    # (1) Remove the fade-out gradient div.
    FADE_OUT = (
        '<div class="bg-linear-to-b pointer-events-none absolute bottom-0 left-0'
        ' z-2 h-1/2 w-full from-transparent to-white dark:to-gray-950"></div>'
    )
    html = html.replace(FADE_OUT, "")

    # (2) Swap the captured rotated-oval halo for the live page's two
    # corner halos (upper-left indigo→pink + lower-right pink→indigo).
    OLD_HALO = (
        '<div class="bg-linear-to-b pointer-events-none absolute inset-x-0 top-1/4'
        ' h-1/2 rotate-6 select-none rounded-[50%] from-pink-500/10 via-white'
        ' to-indigo-500/10 blur-2xl dark:via-gray-950"></div>'
    )
    LIVE_HALOS = (
        '<div class="bg-linear-to-tr pointer-events-none absolute -left-20 top-0'
        ' h-1/2 w-1/2 rounded-r-full from-indigo-500/10 via-white to-pink-500/10'
        ' blur-2xl dark:via-gray-950"></div>'
        '<div class="bg-linear-to-tr pointer-events-none absolute -right-20 bottom-0'
        ' h-1/2 w-1/2 rounded-l-full from-pink-500/10 via-white to-indigo-500/10'
        ' blur-2xl dark:via-gray-950"></div>'
    )
    html = html.replace(OLD_HALO, LIVE_HALOS)


    return html


def replace_orbit_with_graphic(html: str, new_svg: str) -> str:
    """Swap the moon-landing InferenceProviderOrbit SVG for our new graphic.

    The orbit svg starts with `<svg class="hub-orbit ...">` and contains a
    nested <svg> for the HF emoji, so we walk forward counting svg opens
    and closes to find the matching outer </svg>.
    """
    m = re.search(r'<svg\b[^>]*class="hub-orbit\b[^"]*"[^>]*>', html)
    if not m:
        print("  ! couldn't find hub-orbit <svg> — skipping graphic splice")
        return html

    start = m.start()
    depth = 1
    pos = m.end()
    while depth > 0:
        nxt = re.search(r"<(/?)svg\b[^>]*>", html[pos:])
        if not nxt:
            print("  ! ran off the end looking for closing </svg>")
            return html
        depth += -1 if nxt.group(1) == "/" else 1
        pos += nxt.end()
    return html[:start] + new_svg + html[pos:]


def build() -> None:
    if not SOURCE_HTML.exists():
        raise SystemExit(
            f"Missing {SOURCE_HTML.name}. Fetch it with:\n"
            f'  curl -s -o "{SOURCE_HTML}" "{LIVE_BASE}/"'
        )
    if not SOURCE_CSS.exists():
        raise SystemExit(
            f"Missing {SOURCE_CSS.name}. Fetch the compiled CSS first."
        )

    html = SOURCE_HTML.read_text(encoding="utf-8")
    css = SOURCE_CSS.read_text(encoding="utf-8")

    # 1) Force dark mode (live page applies this client-side).
    html = re.sub(r'<html class="">', '<html class="dark" lang="en">', html, count=1)
    # Defensive fallback: also patch <html lang="...">
    if 'class="dark"' not in html[: html.find("<body")]:
        html = re.sub(r"<html\b([^>]*)>", r'<html class="dark"\1>', html, count=1)

    # 2) Point compiled CSS link at the local copy.
    html = re.sub(
        r'<link\s+rel="stylesheet"\s+href="/front/build/[^"]+/style\.css"\s*/?>',
        '<link rel="stylesheet" href="./style.css" />',
        html,
    )

    # 3) Strip framework comments.
    html = strip_framework_comments(html)

    # 4) Strip scripts (static replica).
    html = strip_scripts(html)

    # 5) Swap the InferenceProviderOrbit for our dashed/filled-track graphic.
    html = replace_orbit_with_graphic(html, build_inference_graphic())

    # 5b) Restyle the right-hand Compute section (dotted bg + improved cards).
    html = style_compute_section(html)

    # 5b.i) Strip the 4 hardware pricing cards from the Compute section.
    html = remove_compute_hardware_boxes(html)

    # 5b.ii) Strip the "Starting at $0.60/hour for GPU" caption beside the
    # View pricing button — no longer informative now that the cards are gone.
    html = html.replace(
        '<p class="text-sm text-gray-400 dark:text-gray-500">Starting at $0.60/hour for GPU</p>',
        "",
    )

    # 5b.iii) Drop the Figma-exported Compute Graphic into the empty panel
    # below the heading, plus a radial gradient overlay to fade the top into
    # the page background.
    html = add_compute_graphic(html)

    # 5c) Open Source section: drop the bottom fade, mirror the halo.
    html = style_open_source_section(html)

    # 5d) Enterprise section: left content full-width, boxes moved below
    # the sliding team/org ribbon as a 3-col × 2-row full-width grid.
    html = rearrange_enterprise_section(html)

    # 6) Absolutize remaining /foo refs.
    html = absolutize_attr(html)
    html = absolutize_srcset(html)

    # 6) Rewrite url(...) inside compiled CSS.
    css = absolutize_css_urls(css)

    OUT_HTML.write_text(html, encoding="utf-8")
    OUT_CSS.write_text(css, encoding="utf-8")

    print(f"Wrote {OUT_HTML.name} ({len(html):,} bytes)")
    print(f"Wrote {OUT_CSS.name} ({len(css):,} bytes)")


if __name__ == "__main__":
    build()
