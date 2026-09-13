# Snowbeam pixel logo

<img src="../../src/snowbeam/assets/snowbeam-256.png" alt="Snowbeam pixel logo: a person in a transporter beam dissolving into snow" width="256" height="256" style="image-rendering: pixelated">

The pixel logo is Snowbeam's default mark. A person stands inside a cyan
transporter beam and dissolves into pixels and falling snowflakes.

## Files and use

- [Launcher icon](../../src/snowbeam/assets/snowbeam-512.png): 512px PNG installed by `snowbeam desktop install`.
- [Original generated PNG](pixel/snowbeam-pixel.png): the approved artwork, preserved without changes.
- [Generation prompts and provenance](pixel/README.md).

PNG exports at 16, 24, 32, 48, 64, 128, 256, and 512 pixels are bundled under
`src/snowbeam/assets`. They use nearest-neighbor resizing from the approved
source. No artwork is drawn over the source and no runtime image dependency is
required. The main README uses the same pixel mark.

Run `snowbeam desktop install` after upgrading to refresh a previously installed
Linux launcher icon. Omarchy's [Install → TUI](https://omarchy.org/manual/tuis/#adding-your-own)
flow can also use the supplied PNG. Desktop integration remains an explicit
command; opening Snowbeam never waits for a logo animation.

## Rebuild launcher icons

Use Node.js with `sharp` available:

```sh
node tools/brand/build.cjs --modules /path/to/node_modules
```

The builder only resizes the approved pixel source. It does not regenerate the
design or use the earlier vector artwork.

## Archived vector and motion study

The earlier [vector icon](vector/snowbeam.svg), [transparent mark](snowbeam-mark.svg),
[interactive motion preview](preview.html), [GIF](snowbeam-motion.gif), and
[storyboard](storyboard.png) are retained as an earlier direction. The animation
shows the person and beam vanishing before snowflakes drift down.

Its original geometry and timing remain in `tools/brand/design.cjs`. Rebuild
the archived study separately with Node.js and `sharp`; GIF packing also uses
Python and Pillow:

```sh
node tools/brand/build-vector.cjs --modules /path/to/node_modules --frames /tmp/snowbeam-frames
python tools/brand/pack.py --frames /tmp/snowbeam-frames
```

Archived vector exports go to `docs/brand/vector`, so rebuilding the study does
not replace the active pixel launcher icons.
