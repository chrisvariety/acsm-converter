// Removes the DRM from one ADEPT PDF, off the main thread. Started by
// runPdfWorker() in convert.js with {file, key, module, wasm}; answers
// {ok: true, pdf: Uint8Array} or {ok: false, code}.
//
// The work is done by pdf_decrypt.wasm (wasm/pdf_decrypt.cpp): libgourou's own
// PDF code, the same the server runs. One worker per book, so every book
// starts with a fresh heap and everything is freed when the page terminates
// the worker.

// Result codes of pdf_decrypt(), from wasm/pdf_decrypt.cpp.
const CODES = { 1: "bad_key", 2: "not_adept", 3: "unsupported", 4: "failed" };

self.onmessage = async (e) => {
  const { file, key, module, wasm } = e.data;
  try {
    importScripts(module);
    const stderr = [];
    const M = await createPdfDecrypt({
      locateFile: () => wasm,
      print: () => {},
      printErr: (line) => stderr.push(line),
    });

    // WORKERFS reads the File in place as the parser seeks around it, rather
    // than copying the whole book into memory first.
    M.FS.mkdir("/in");
    M.FS.mount(M.WORKERFS, { blobs: [{ name: "book.pdf", data: file }] }, "/in");

    const rc = M.ccall("pdf_decrypt", "number", ["string", "string", "string"],
      ["/in/book.pdf", "/out.pdf", key]);
    if (rc !== 0) {
      self.postMessage({ ok: false, code: CODES[rc] || "rc" + rc });
      return;
    }

    const pdf = M.FS.readFile("/out.pdf");
    M.FS.unlink("/out.pdf");
    // The writer reported success, but a book we can't vouch for is worse
    // than the upload the page falls back to.
    if (pdf.byteLength < 5 || String.fromCharCode(...pdf.subarray(0, 5)) !== "%PDF-") {
      self.postMessage({ ok: false, code: "bad_output" });
      return;
    }
    self.postMessage({ ok: true, pdf: pdf }, [pdf.buffer]);
  } catch (err) {
    // Emscripten aborts (out of memory included) land here as exceptions.
    self.postMessage({ ok: false, code: "exception" });
  }
};
