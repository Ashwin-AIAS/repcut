# Typefaces

Two faces, both **SIL Open Font License 1.1**, committed here as latin-subset
`woff2` and loaded through `next/font/local`.

| File | Family | Weight | Role |
|---|---|---|---|
| `Sora-SemiBold.woff2` | Sora | 600 | Display — headings, wordmark, numerals in the player |
| `IBMPlexSans-Regular.woff2` | IBM Plex Sans | 400 | UI — body copy, labels |
| `IBMPlexSans-Medium.woff2` | IBM Plex Sans | 500 | UI — controls, active states |
| `IBMPlexSans-SemiBold.woff2` | IBM Plex Sans | 600 | UI — emphasis, table headers |

## Provenance

Extracted from the `@fontsource` packages at the versions below, which repackage
the Google Fonts releases. The packages were installed with `--no-save` and are
**not** dependencies — they were the delivery vehicle, not something the build
needs. Nothing is fetched at build time, so `next build` works with no network.

| Source package | Version | Font version | Upstream |
|---|---|---|---|
| `@fontsource/sora` | 5.3.0 | v17 | https://github.com/google/fonts |
| `@fontsource/ibm-plex-sans` | 5.3.0 | v23 | https://github.com/google/fonts |

To refresh:

```
npm install --no-save @fontsource/sora@<v> @fontsource/ibm-plex-sans@<v>
cp node_modules/@fontsource/sora/files/sora-latin-600-normal.woff2 \
   app/fonts/Sora-SemiBold.woff2
# …and the three ibm-plex-sans-latin-{400,500,600}-normal.woff2 files
```

## Licence

`Sora-OFL.txt` and `IBMPlexSans-OFL.txt` are the licence texts, kept beside the
files because **OFL 1.1 requires the copyright notice and licence to be
distributed with the font software**. Do not remove them.

- Sora — Copyright 2019 The Sora Project Authors
- IBM Plex Sans — Copyright 2019 IBM Corp.

OFL-1.1 is AGPL-3.0 compatible, and neither face carries a reserved font name
that our usage triggers: the files are unmodified and are served under their own
names. See `.claude/rules/frontend-and-licensing.md`.

## Why these two

`ui/` is bound to `.claude/skills/repcut-design-system`, and the build guide's
Deliverable 5 asks for "one distinctive display face + one workhorse UI face,
not defaults". Sora's geometric, slightly narrow caps give the display role a
character that a system stack cannot; Plex Sans is a genuine workhorse with a
large x-height and unambiguous `1/l/I`, which matters in a UI that renders
timecodes, frame rates and hash prefixes.

Neither is copied from another product's brand, and no product's exact colour,
icon or type choices are reproduced anywhere in this repo.
