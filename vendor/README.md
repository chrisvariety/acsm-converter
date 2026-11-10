# Vendored dependencies

`forge.soutade.fr` (the upstream host for libgourou and uPDFParser) has been
unreliable, so these sources are vendored here and built directly in the
Dockerfile instead of being cloned at build time. No network access to the
forge is required to build the image.

## libgourou/

Upstream: https://forge.soutade.fr/soutade/libgourou.git
Commit:   324c566 ("Fix help of adept_load_mgt", v0.8.9)

Vendored verbatim (build artifacts excluded: `obj/`, compiled `utils/*` binaries),
with these local patches:

- **Split fulfill and download** (for the browser-assisted download in
  `scripts/server.py`, used when Google Play answers our download with 429):
  - `FulfillmentItem::getFulfillmentXML()` returns the fulfill reply an item
    was built from; passing it back to the `FulfillmentItem` constructor
    rebuilds an equivalent item (license, rights, metadata).
  - `DRMProcessor::download()` is split: the post-download half (adding
    `META-INF/rights.xml` to an EPUB, or the EBX handler to a PDF) is now the
    public `DRMProcessor::addRightsToFile()`, and `download()` calls it. No
    behaviour change for `download()` itself.
  - `acsmdownloader` gets `--save-fulfillment <file>` (write the fulfill reply
    before downloading, so it survives a refused download) and
    `--from-file <book> --fulfillment <file>` (skip the network: finish a book
    file obtained elsewhere exactly as a download would be finished, including
    the `Created <name>` line and file naming).

- **Book key without the book** (for PDFs decrypted in the user's browser,
  `POST /finish/key` in `scripts/server.py`): `DRMProcessor::getBookKey()`
  returns the key of a fulfilled item, through the same `decryptADEPTKey()`
  that `removeDRM()` uses (user check and hardened-key first pass included).
  `acsmdownloader --fulfillment <file> --book-key` prints it as
  `Book key <32 hex digits>`, followed by `Book name <title>`, the name
  `--from-file` would give the file before its extension. No ACSM, no network.

- **PDF decryption split out and fixed** (`src/pdf_ebx.cpp`, `include/pdf_ebx.h`):
  the part of `DRMProcessor::removePDFDRM` that runs once the book key is known
  (decrypt every string and stream, drop the `EBX_HANDLER` dictionary) now lives
  in its own file that depends on uPDFParser alone, taking MD5 and RC4 through
  a small `EBXCrypto` interface. `removePDFDRM` calls it with the DRM client
  behind that interface; `wasm/pdf_decrypt.cpp` at the top of the repository
  compiles the same file to WebAssembly for the browser. Finding the handler,
  the version check, reading `ADEPT_LICENSE` and unwrapping the key are
  unchanged and stay in `libgourou.cpp`. Two fixes came with the move:
  - Strings are decrypted wherever they are in an object. Upstream only looked
    at the top level of each object's dictionary, so strings inside arrays and
    nested dictionaries (`/Dest [...]`, `/A << /D (...) >>`, name trees) and
    objects that are a bare string or array were written out still encrypted:
    link and bookmark targets came out as garbage.
  - Literal strings are fully unescaped before decryption (octal `\ddd`, `\t`,
    `\b`, `\f`, line continuations), where `String::unescapedValue()` handles
    `\\ \( \) \n \r` only. Decrypted strings are written back as hexadecimal
    strings, since `String::str()` escapes parentheses and nothing else.

- `utils/drmprocessorclientimpl.cpp` (`sendRequest`): the `CLIENT_HTTP_ERROR`
  message carries the request URL, i.e. `HTTP Error code 429 <URL>` instead of
  `HTTP Error code 429`. A single acsmdownloader run makes several requests
  (operator `/Auth`, `/InitLicenseService`, `/Fulfill`, `/LicenseServiceInfo`,
  notify, then the book download), and without the URL a failure can't be tied
  to one of them. We added it to find out which step Google Play answers with
  429. `-v` would also log the URLs, but it dumps the whole activation.xml (device
  pkcs12 and private license key) via `User::updateActivationFile`, so it is
  not an option. server.py matches these errors by substring
  (`"HTTP Error code 429"` etc.), so the suffix doesn't affect the error cards.

- Backported from upstream 4e1ccfc ("Fix memory management errors"), only
  this hunk: `DRMProcessor::fulfill()` appends a NUL to the fulfill reply
  before `pugi::xml_document::load_string()`. The reply `ByteArray` is built
  from a `std::string` without its terminator, so `load_string` read past the
  end of the heap buffer (heap-buffer-overflow under ASan) on every fulfill,
  right after `/Fulfill` has spent the single-use token.

- Backported from upstream 4e1ccfc, only this hunk: `parseDateTime()` zeroes
  its `struct tm` before `strptime()`. Left uninitialised, `tm_isdst` held
  stack garbage; when positive, glibc's `mktime()` in our UTC container
  returns -1, so `fulfill()` logged "WARNING: ACSM file expired (...)" for
  ACSMs that had not expired.

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
