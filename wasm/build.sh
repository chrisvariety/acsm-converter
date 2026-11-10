#!/bin/sh
# Build the browser-side PDF decryptor: pdf_decrypt.js + pdf_decrypt.wasm.
#
#   wasm/build.sh <output directory>
#
# Needs em++ (Emscripten) on PATH; the Dockerfile runs it in emscripten/emsdk.
# Run from the repository root.
set -eu

OUT=${1:?usage: wasm/build.sh <output directory>}
GOUROU=vendor/libgourou

mkdir -p "$OUT"

# -fexceptions:        uPDFParser reports malformed input by throwing.
# MODULARIZE:          createPdfDecrypt() returns a fresh instance, so the
#                      worker starts every book with an empty heap.
# ALLOW_MEMORY_GROWTH: the heap holds each decrypted stream until the file is
#                      written, i.e. about the size of the book.
# WORKERFS:            reads the user's File in place, without copying it in.
# NODEFS:              only for tests run under Node.
em++ -O2 -fexceptions \
  -I"$GOUROU/include" -I"$GOUROU/lib/updfparser/include" \
  wasm/pdf_decrypt.cpp \
  "$GOUROU/src/pdf_ebx.cpp" \
  "$GOUROU/lib/updfparser/src/uPDFParser.cpp" \
  "$GOUROU/lib/updfparser/src/uPDFTypes.cpp" \
  -o "$OUT/pdf_decrypt.js" \
  --no-entry \
  -sMODULARIZE=1 -sEXPORT_NAME=createPdfDecrypt \
  -sENVIRONMENT=worker,node \
  -sALLOW_MEMORY_GROWTH=1 -sMAXIMUM_MEMORY=4GB \
  -sFORCE_FILESYSTEM=1 -lworkerfs.js -lnodefs.js \
  -sEXPORTED_FUNCTIONS=_pdf_decrypt \
  -sEXPORTED_RUNTIME_METHODS=ccall,FS,WORKERFS,NODEFS
