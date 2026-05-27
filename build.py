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
