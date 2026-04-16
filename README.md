# Solemn Font Builder

This repository keeps a small Python/uv helper around a sparse checkout of
Sarasa Gothic. Downloaded font archives and extracted build inputs are treated
as intermediates and are not committed here.

## Fetch sources

```bash
env UV_CACHE_DIR=.cache/uv uv run python src/solemn_fonts/cli.py download --dry-run
env UV_CACHE_DIR=.cache/uv uv run python src/solemn_fonts/cli.py download
```

Defaults are tuned for an IDE font:

- Iosevka `SS17` TTF package, materialized as Sarasa's `IosevkaN` source group.
- Source Han Sans `SC` package only.
- No Term/Fixed/Slab sources unless explicitly requested.

To also fetch Term sources:

```bash
env UV_CACHE_DIR=.cache/uv uv run python src/solemn_fonts/cli.py download --with-term
```

The cache lives under `.cache/solemn-fonts`. Materialized source files are
written under `.cache/solemn-fonts/sources/` so they stay build intermediates
outside the Sarasa submodule.

## Build minimal Sarasa Mono SC

```bash
env UV_CACHE_DIR=.cache/uv uv run python src/solemn_fonts/cli.py build --dry-run
env UV_CACHE_DIR=.cache/uv uv run python src/solemn_fonts/cli.py build
```

Sarasa is kept as an upstream submodule. The local minimal build changes live
in `patches/sarasa-minimal-mono-sc.patch`, and the build command applies that
patch to `vendor/Sarasa-Gothic` before invoking Verda.

The build command checks for `node` and `npm`. It gets AFDKO's `otf2ttf` and
`otc2otf` from the uv dependency environment, downloads `ttfautohint` 1.8.4
into `.cache/solemn-fonts/tools` when it is not already on PATH, checks `zstd`, runs
`npm install` in the Sarasa checkout, points Sarasa at
`.cache/solemn-fonts/sources`, and then asks Verda for only these
default targets:

- `out/TTF/SarasaMonoSC-Regular.ttf`
- `out/TTF/SarasaMonoSC-Italic.ttf`
- `out/TTF/SarasaMonoSC-Bold.ttf`
- `out/TTF/SarasaMonoSC-BoldItalic.ttf`

Use `--all-styles` to build every Sarasa Mono style, or `--styles Regular Bold`
to pick an explicit subset. The build always produces hinted TTFs.

After a successful build, the helper copies the four default TTFs into
`release/` and runs `release/pack.sh` to refresh `release/release.tar.zstd`.
