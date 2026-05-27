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
Source:   github.com/loganpowell/knock-lambda (deps/uPDFParser)

Version matters here: libgourou 324c566 calls `uPDFParser::Parser::removeObject()`
(in `removePDFDRM`), which only exists in a recent uPDFParser. Older mirrors —
`SamuelMarks/updfparser` @26b1e0d and the byte-identical `drazulay/updfparser` —
predate that method, so libgourou fails to compile against them:

    error: 'class uPDFParser::Parser' has no member named 'removeObject'

The knock-lambda copy tracks the newer upstream that adds `removeObject`
(alongside `prevChar`/`writeBuffer` and some CR/LF parsing fixes) and is the
correct counterpart to the pinned libgourou. We don't use libgourou's PDF path,
but the reference is unconditional, so the symbol must be present to link.

The stock `Makefile` build (`make BUILD_STATIC=1 BUILD_SHARED=0`) is used; the
bundled CMake files are inert.
