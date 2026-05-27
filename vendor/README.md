# Vendored dependencies

`forge.soutade.fr` (the upstream host for libgourou and uPDFParser) has been
unreliable, so these sources are vendored here and built directly in the
Dockerfile instead of being cloned at build time. No network access to the
forge is required to build the image.

## libgourou/

Upstream: https://forge.soutade.fr/soutade/libgourou.git
Commit:   324c566 ("Fix help of adept_load_mgt", v0.8.9)

Vendored verbatim (build artifacts excluded: `obj/`, compiled `utils/*` binaries).

## libgourou/lib/updfparser/

libgourou's `scripts/setup.sh` normally clones uPDFParser into `lib/updfparser`
and builds it. We pre-place it here so that step is skipped (the script only
clones when the directory is absent).

Upstream: https://forge.soutade.fr/soutade/uPDFParser.git
Commit:   6060d12 ("Add removeObject() method to parser")

Version matters here: libgourou 324c566 calls `uPDFParser::Parser::removeObject()`
(in `removePDFDRM`), which was only added in upstream commit 6060d12 (current
HEAD). Older mirrors — `SamuelMarks/updfparser` @26b1e0d and the byte-identical
`drazulay/updfparser` — predate that commit, so libgourou fails to compile
against them:

    error: 'class uPDFParser::Parser' has no member named 'removeObject'

We don't use libgourou's PDF path, but the reference is unconditional, so the
symbol must be present to link. The vendored sources here are byte-identical to
upstream HEAD (`src/`, `include/`, `Makefile`).

The stock `Makefile` build (`make BUILD_STATIC=1 BUILD_SHARED=0`) is used; the
bundled CMake files are inert.
