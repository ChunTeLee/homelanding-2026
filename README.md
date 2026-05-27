# homelanding 2026

Pixel-perfect static replica of the new HF homepage with two design
explorations layered on top:

- **Inference Providers section** — the spinning orbit visual is replaced by a
  dashed/filled provider-track illustration. A soft-blurred mask glides left
  → right along the central S-curve via SMIL `<animateMotion>`, briefly
  igniting each provider's brand colour as it passes.
- **Compute section** — the purple linear gradient is replaced by a fine
  purple dot pattern with a "light source" centred on the first row of
  hardware cards; cards are restyled as outlined regions on the page
  background, with the price pills snapped to the right edge of each card.

Live preview: https://chuntelee.github.io/homelanding-2026/

## Files

| File | What it is |
| --- | --- |
| `index.html` | Built replica entry point (open in browser / serve statically). |
| `style.css` | Compiled CSS pulled from moon-landing, with relative `url(...)` references rewritten to absolute. |
| `build.py` | Reproducible build pipeline. |
| `page-source.html` | Raw SSR shell from the live URL (input to `build.py`). |
| `dashed-dark.svg`, `filled-dark.svg` | Figma-exported assets for the inference graphic. |

## Rebuild

```sh
# Refresh the SSR shell and compiled CSS from the live URL (requires HF VPN):
curl -s -o page-source.html "https://new-ui-homepage-ff.us.dev.moon.huggingface.tech/"
curl -s -o style.css       "https://new-ui-homepage-ff.us.dev.moon.huggingface.tech/front/build/kube-e90c65b/style.css"

# Re-run the pipeline:
python build.py

# Serve locally:
python -m http.server 7800
```
