// Shared by every page that runs a conversion: POSTs a file to /convert (an
// ACSM) or /finish (a book the user's browser downloaded), follows the NDJSON
// progress stream, and saves the finished book. Pages decide what happens
// around it through the callbacks passed to runConversion.

const STAGE_LABELS = {
  hydrate: "Starting...",
  activate: "Preparing...",
  download: "Processing file...",
  prepare: "Checking your file...",
  decrypt: "Finalizing...",
};

// runConversion(url, body, opts) -> Promise<"done" | "manual" | "error">
//
//   opts.status            element that shows progress, then the outcome
//   opts.onManualDownload  (event) => void; the provider refused our download
//                          and the user's browser has to fetch the book
//   opts.onError           (event) => bool; return true if the page handled
//                          the error itself, false for the standard error card
//
// eventLog holds the raw NDJSON lines of the run, surfaced via "Show log" on
// failure so users can copy the same stream they'd see in dev tools.
async function runConversion(url, body, opts) {
  const status = opts.status;
  const eventLog = [];
  setStatus(status, "Uploading...");
  try {
    const resp = await fetch(url, { method: "POST", body: body });
    if (!resp.ok) {
      const err = await resp.json().catch(() => null);
      showPlainError(status, err && err.error ? err.error : "Conversion failed: " + resp.statusText, eventLog);
      return "error";
    }

    let resultEvent = null;
    let errorEvent = null;
    let manualEvent = null;
    const payload = await readStream(resp.body, eventLog, (event) => {
      switch (event.type) {
        case "queued":
          setStatus(status, "Queued...");
          break;
        case "waiting": {
          // Two separate queues: downloads run several at a time, DRM
          // removal is strictly one at a time. Both send the same event
          // shape, so a missing stage/position stays harmless.
          const where = event.stage === "decrypt" ? "Waiting to finalize" : "Waiting in queue";
          const pos = event.queue_position ? " #" + event.queue_position : "";
          setStatus(status, where + pos + " (" + event.seconds_waited + "s)...");
          break;
        }
        case "status":
          setStatus(status, STAGE_LABELS[event.stage] || event.message || "Working...");
          break;
        case "result":
          resultEvent = event;
          break;
        case "error":
          errorEvent = event;
          break;
        case "manual_download":
          manualEvent = event;
          break;
      }
    });

    if (resultEvent) {
      // The stream is delimited by connection close, so a drop mid-transfer
      // looks exactly like a clean end. Without this check we would hand the
      // user a truncated file that opens as a corrupt book.
      const got = payload ? payload.received : 0;
      if (got !== resultEvent.size) {
        showPlainError(status,
          "Download incomplete: received " + got + " of " + resultEvent.size +
          " bytes. The connection dropped before the whole book arrived. " +
          "Please try again.", eventLog);
        return "error";
      }
      saveBook(payload.parts, resultEvent);
      setStatus(status, "Done! Your file is downloading.");
      status.className = "done";
      showDonate(status);
      return "done";
    }
    if (manualEvent) {
      opts.onManualDownload(manualEvent);
      return "manual";
    }
    if (errorEvent) {
      if (opts.onError && opts.onError(errorEvent)) return "error";
      if (errorEvent.error_code) {
        showKnownError(status, errorEvent, eventLog);
      } else {
        const parts = [errorEvent.error || "Conversion failed"];
        if (errorEvent.stdout) parts.push("stdout: " + errorEvent.stdout);
        if (errorEvent.stderr) parts.push("stderr: " + errorEvent.stderr);
        showPlainError(status, parts.join("\n"), eventLog);
      }
      return "error";
    }
    showPlainError(status, "Conversion ended without a result.", eventLog);
    return "error";
  } catch (err) {
    showPlainError(status, err.name + ": " + err.message, eventLog);
    return "error";
  }
}

// decryptPdfInBrowser(file, token, assets, status) -> Promise<bool>
//
// The /finish flow without the upload, for PDFs: the server hands over the key
// to this one book (/finish/key) and a worker removes the DRM from the file
// right here (pdf-worker.js, running the same libgourou code as the server,
// compiled to WebAssembly). Books can be hundreds of MB, which is slow to
// upload and more than the server wants to hold.
//
// Resolves true once the book has been saved. Resolves false on *any*
// failure -- not a PDF, no WebAssembly, no key, out of memory, a file the
// parser rejects -- having changed nothing, so the caller uploads the file to
// /finish exactly as before and the server gives its own verdict. This path
// is only ever a shortcut.
//
//   assets   {worker, module, wasm}: URLs of pdf-worker.js, pdf_decrypt.js
//            and pdf_decrypt.wasm
async function decryptPdfInBrowser(file, token, assets, status) {
  const base = "/finish/";
  const query = "?token=" + encodeURIComponent(token);
  let stage = "unsupported";
  try {
    if (!assets || typeof Worker === "undefined" || typeof WebAssembly === "undefined") return false;
    const magic = new Uint8Array(await file.slice(0, 5).arrayBuffer());
    if (new TextDecoder().decode(magic) !== "%PDF-") return false;

    setStatus(status, "Checking your file...");
    stage = "key";
    const resp = await fetch(base + "key" + query, { method: "POST" });
    if (!resp.ok) return false;   // expired tokens included: /finish says so properly
    const book = await resp.json();

    setStatus(status, "Finalizing on your device...");
    stage = "worker";
    const pdf = await runPdfWorker(file, book.key, assets);

    saveBook([pdf], { content_type: "application/pdf", filename: book.filename });
    setStatus(status, "Done! Your file is downloading.");
    status.className = "done";
    showDonate(status);
    // Best-effort: the book is already saved.
    fetch(base + "done" + query + "&size=" + pdf.byteLength, { method: "POST" }).catch(() => {});
    return true;
  } catch (err) {
    const code = stage + "_" + String((err && err.code) || "exception");
    fetch(base + "done" + query + "&error=" + encodeURIComponent(code), { method: "POST" }).catch(() => {});
    return false;
  }
}

// Runs one book through pdf-worker.js; resolves with the decrypted PDF as a
// Uint8Array, rejects with an Error carrying a short `code`.
function runPdfWorker(file, key, assets) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(assets.worker);
    const fail = (code) => {
      worker.terminate();
      const err = new Error("pdf worker: " + code);
      err.code = code;
      reject(err);
    };
    worker.onmessage = (e) => {
      if (!e.data.ok) return fail(e.data.code);
      worker.terminate();
      resolve(e.data.pdf);
    };
    // A wasm abort (out of memory, most likely) or a script that won't load.
    worker.onerror = (e) => { e.preventDefault(); fail("crash"); };
    worker.postMessage({ file: file, key: key, module: assets.module, wasm: assets.wasm });
  });
}

function saveBook(parts, resultEvent) {
  const blob = new Blob(parts, { type: resultEvent.content_type });
  // Drop our references now the Blob owns the data; large blobs are backed by
  // disk, so this is what keeps the JS heap flat.
  parts.length = 0;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = resultEvent.filename || "output.epub";
  a.click();
  // Revoking straight after click() races the start of the download on a
  // multi-hundred-MB blob, which is exactly the size we are here to support.
  // Hold the URL long enough for the browser to take it.
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// Reads the response: NDJSON progress events, then -- once the terminal
// "result" event arrives -- every remaining byte as the file itself.
// Returns {parts, received} for the payload, or null if no result was sent.
//
// This walks raw bytes rather than a decoded string for two reasons. The
// payload is binary, so a TextDecoder over it would corrupt it. And a string
// version is quadratic: appending every chunk to one buffer and calling
// indexOf("\n") on the whole thing per read made the single huge result line
// rescan ~1.5TB before it even ran out of memory. Here the line buffer only
// ever holds small event lines.
async function readStream(body, eventLog, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = new Uint8Array(0);
  let parts = null;          // non-null once we are past the result event
  let received = 0;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;

    if (parts) {
      parts.push(value);
      received += value.byteLength;
      continue;
    }

    pending = concatBytes(pending, value);
    let start = 0;
    for (;;) {
      const nl = pending.indexOf(10, start);   // "\n"
      if (nl < 0) break;
      const line = decoder.decode(pending.subarray(start, nl)).trim();
      start = nl + 1;
      if (!line) continue;
      eventLog.push(line);
      let event = null;
      try {
        event = JSON.parse(line);
        onEvent(event);
      } catch (_) {
        // A malformed line, or a status handler that threw, must not abort a
        // transfer that is otherwise fine. If the line did parse we still act
        // on it below -- only the display side is skipped.
      }
      if (!event) continue;
      if (event.type === "result") {
        // Everything after this line is the book. Take whatever of it already
        // landed in this chunk and switch to binary mode.
        const rest = pending.subarray(start);
        parts = [];
        if (rest.byteLength) {
          parts.push(rest.slice());
          received += rest.byteLength;
        }
        break;
      }
    }
    if (parts) {
      pending = null;
    } else {
      pending = pending.slice(start);
    }
  }
  return parts ? { parts: parts, received: received } : null;
}

function concatBytes(a, b) {
  if (!a.byteLength) return b;
  const out = new Uint8Array(a.byteLength + b.byteLength);
  out.set(a, 0);
  out.set(b, a.byteLength);
  return out;
}

function setStatus(status, msg) {
  status.textContent = msg;
  status.className = "";
  status.style.whiteSpace = "";
}

function showPlainError(status, msg, eventLog) {
  status.className = "error";
  status.style.whiteSpace = "";
  status.innerHTML = "";
  const detail = document.createElement("div");
  detail.style.whiteSpace = "pre-wrap";
  detail.innerText = msg;
  status.appendChild(detail);
  // Plain errors carry no E_ code (timeouts, crashes, network failures,
  // "ended without a result"), so always offer the support contact.
  showLogControls(status, true, eventLog);
}

function showKnownError(status, err, eventLog) {
  let html = '<div class="error-detail">';
  html += "<h2>" + esc(err.title) + "</h2>";
  html += "<code>" + esc(err.error_code) + "</code>";
  html += "<p>" + esc(err.description) + "</p>";
  if (err.solutions && err.solutions.length) {
    html += "<ul>";
    for (const s of err.solutions) {
      html += "<li><h3>" + esc(s.heading) + "</h3><p>" + esc(s.text) + "</p></li>";
    }
    html += "</ul>";
  }
  html += "</div>";
  status.innerHTML = html;
  status.className = "";
  // E_* codes are well-understood provider/account errors with self-service
  // fixes above, transient:true marks upstream outages that clear on their
  // own, and user_fixable:true marks a user mistake the card explains (e.g.
  // uploading the book instead of the .acsm) -- none of these warrant an
  // issue. Everything else (timeouts, crashes, rate limits, unknowns) is
  // worth flagging to me.
  const isExpected =
    err.transient === true || err.user_fixable === true ||
    (err.error_code && err.error_code.startsWith("E_"));
  showLogControls(status, !isExpected, eventLog);
}

// Append a "Show log" toggle (and, for unexpected errors, a link to file a
// GitHub issue) below whatever error message is already in `status`.
function showLogControls(status, showSupport, eventLog) {
  const controls = document.createElement("div");
  controls.className = "log-controls";

  if (showSupport) {
    const support = document.createElement("p");
    support.className = "support";
    support.innerHTML =
      'Still stuck? Did you read the error message and try again after a few minutes? <a href="https://github.com/chrisvariety/acsm-converter/issues/new" target="_blank" rel="noopener">Open an issue on GitHub</a> for support, and include the log below along with any info on the book you were trying to convert e.g. where you got it, what kind of book it is (for example, a novel or a textbook or a cookbook), and any other relevant details.';
    controls.appendChild(support);
  }

  if (eventLog.length) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "log-toggle";
    btn.textContent = "Show log";

    const pre = document.createElement("pre");
    pre.className = "log-output";
    pre.style.display = "none";
    pre.textContent = eventLog.join("\n");

    btn.addEventListener("click", () => {
      const hidden = pre.style.display === "none";
      pre.style.display = hidden ? "block" : "none";
      btn.textContent = hidden ? "Hide log" : "Show log";
    });

    controls.appendChild(btn);
    controls.appendChild(pre);
  }

  status.appendChild(controls);
}

// A gentle, optional ask once the book is safely on its way.
function showDonate(status) {
  const box = document.createElement("div");
  box.className = "donate";
  box.innerHTML =
    '<p><strong>Did it work? Great!</strong> To help with hosting and ' +
    "development costs, you can optionally buy me a coffee on Ko-fi.</p>" +
    '<a class="donate-link" href="https://ko-fi.com/chrisvariety" target="_blank" rel="noopener">' +
    '<span class="donate-heart" aria-hidden="true">&#9829;</span> Donate on Ko-fi</a>';
  status.appendChild(box);
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
