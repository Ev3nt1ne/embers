# Embers: brand notes

The idea: the "…" of an agent that is waiting, where the last dot is still burning. The two dots and the flame
have the same base radius, and the flame stands where the third dot would be.

## Files

| File | Use |
|---|---|
| `embers-symbol-*.svg` | The wide symbol (about 2 : 1). Use it wherever width allows: inline, in plugin listings that take a wide image. |
| `embers-square-*.svg` | The square cut. Use it for avatars and tiles. It fits inside a circle crop. |
| `embers-small-*.svg` | The small cut, drawn on a 16-unit grid. Use it below 32 px: favicon, terminal-size glyph. `-amber` is its one-colour version. |
| `embers-lockup-*.svg`, `*-1200.png` | The README header: the word, then the dots and the flame. |
| `social-preview-dark.png` (recommended), `social-preview-light.png` | GitHub social preview, 1280×640. The SVG sources are next to them. |
| `icons/` | `favicon.svg`, `favicon.ico`, `favicon-16/32/48.png`, `apple-touch-icon.png`, `icon-192/512.png`, `maskable-512.png`, `avatar-512.png`, `app-icon.svg` and `app-icon-512.png`, `site.webmanifest`, `head-snippet.html` |

Each piece comes in four colourings: `-color` for light backgrounds, `-color-dark` for dark ones, and `-black` and
`-white` in one colour.

## Usage notes

- **Colours**:
  - ember amber `#EE9B22`, for the flame only;
  - charcoal `#2A2522`, for the dots and the word on light backgrounds;
  - ivory `#F3EDE4`, for the dots and the word on dark backgrounds.
- **One colour**: everything in one ink (black, white or amber). The flame keeps its shape, so the mark still
  reads.
- **Which piece at which size**:
  - from 32 px up, the square cut or the wide symbol;
  - below 32 px, the small cut;
  - the lockup no narrower than 120 px.
- **Clear space** around the lockup and the symbol: one dot diameter on every side. The lockup files already
  include it.
- **The lockup**:
  - The word is Inter Display SemiBold, outlined, with tracking −1 %. Its licence is the SIL Open Font License 1.1,
    which allows use in a logo.
  - The dots sit on the baseline, with the gap between the word and the first dot 1.25× the gap between the dots.
  - The flame keeps the symbol's proportions.
  - Do not retype the word, re-space it, or move the row.
- **README header**: use a `<picture>` element, so GitHub's dark theme gets the ivory version:
  ```html
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="embers-lockup-color-dark.svg">
    <img alt="Embers" src="embers-lockup-color.svg" width="380">
  </picture>
  ```
- **Website head**: `icons/head-snippet.html`. `favicon.svg` turns the dots ivory when the browser is in dark mode.
  The `.ico` and PNG fallback is all amber, because it cannot switch.
- **Don't**:
  - recolour the dots amber in the colour version, because only the last dot burns;
  - make the dots and the flame base different sizes;
  - add a glow or a gradient;
  - put the mark on a busy background without the charcoal tile.
