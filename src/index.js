const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ACSM Converter</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 0 auto; padding: 60px 20px; color: #222; }
    h1 { font-size: 1.75rem; margin-bottom: 0.25rem; }
    .subtitle { color: #555; font-size: 1.05rem; margin-top: 0; }

    .steps { display: flex; gap: 1.5rem; margin: 2.5rem 0; }
    .step { flex: 1; text-align: center; }
    .step-num { display: inline-flex; align-items: center; justify-content: center; width: 2rem; height: 2rem; border-radius: 50%; background: #1a1a2e; color: #fff; font-weight: 600; font-size: 0.9rem; margin-bottom: 0.5rem; }
    .step-title { font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem; }
    .step-desc { font-size: 0.8rem; color: #666; }

    form { margin: 1.5rem 0; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
    input[type="file"] { flex: 1; min-width: 200px; }
    button { padding: 0.5rem 1.5rem; font-size: 1rem; cursor: pointer; background: #1a1a2e; color: #fff; border: none; border-radius: 4px; }
    button:hover { background: #2d2d4e; }
    button:disabled { opacity: 0.6; cursor: not-allowed; }

    #status { margin-top: 1rem; color: #666; }

    .features { display: flex; gap: 2rem; margin-top: 3rem; padding-top: 2rem; border-top: 1px solid #eee; }
    .feature { flex: 1; }
    .feature h3 { font-size: 1rem; margin: 0 0 0.25rem; }
    .feature p { font-size: 0.85rem; color: #555; margin: 0; }

    .error { color: #c00; }
    .error-detail { margin-top: 1rem; background: #fff5f5; border: 1px solid #e8c0c0; border-radius: 6px; padding: 1.25rem; text-align: left; }
    .error-detail h2 { font-size: 1.1rem; margin: 0 0 0.25rem; color: #900; }
    .error-detail code { font-size: 0.85rem; color: #666; }
    .error-detail p { margin: 0.75rem 0; color: #333; }
    .error-detail h3 { font-size: 0.95rem; margin: 1rem 0 0.25rem; color: #444; }
    .error-detail ul { margin: 0; padding-left: 1.25rem; }
    .error-detail li { margin: 0.5rem 0; color: #333; }

    .log-controls { margin-top: 1rem; text-align: left; }
    .log-controls .support { font-size: 0.9rem; color: #333; margin: 0 0 0.75rem; }
    .log-controls .support a { color: #1a1a2e; }
    .log-toggle { padding: 0.3rem 0.9rem; font-size: 0.85rem; background: #555; }
    .log-toggle:hover { background: #333; }
    .log-output { margin-top: 0.75rem; background: #1a1a2e; color: #e0e0e0; padding: 0.75rem 1rem; border-radius: 4px; font-size: 0.8rem; line-height: 1.4; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; }
  </style>
</head>
<body>
  <h1>ACSM Converter</h1>
  <p class="subtitle">Turn your ACSM files into readable EPUBs and PDFs, right in the browser.</p>

  <div class="steps">
    <div class="step">
      <div class="step-num">1</div>
      <div class="step-title">Choose</div>
      <div class="step-desc">Pick an <code>.acsm</code> file from your device</div>
    </div>
    <div class="step">
      <div class="step-num">2</div>
      <div class="step-title">Convert</div>
      <div class="step-desc">We handle the download and conversion for you</div>
    </div>
    <div class="step">
      <div class="step-num">3</div>
      <div class="step-title">Read</div>
      <div class="step-desc">Get a standard EPUB or PDF you can open anywhere</div>
    </div>
  </div>

  <form id="form">
    <input type="file" name="file" accept=".acsm" required />
    <button type="submit">Convert</button>
  </form>
  <div id="status"></div>

  <div class="features">
    <div class="feature">
      <h3>No setup required</h3>
      <p>No accounts, no installs, no Adobe Digital Editions. Just upload and go.</p>
    </div>
    <div class="feature">
      <h3>Nothing stored</h3>
      <p>Files are processed in memory and discarded immediately. We don't keep your books or your data.</p>
    </div>
  </div>
  <script>
    const form = document.getElementById("form");
    const status = document.getElementById("status");

    // Raw NDJSON lines for the current conversion, surfaced via "Show log" on
    // failure so users can copy the same stream they'd see in dev tools.
    let eventLog = [];

    const STAGE_LABELS = {
      hydrate: "Starting...",
      activate: "Preparing...",
      download: "Processing file...",
      decrypt: "Finalizing...",
    };

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = form.file.files[0];
      if (!file) return;
      eventLog = [];
      setStatus("Uploading...");
      try {
        const resp = await fetch("/convert", {
          method: "POST",
          body: await file.arrayBuffer(),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => null);
          showPlainError(err && err.error ? err.error : "Conversion failed: " + resp.statusText);
          return;
        }

        let resultEvent = null;
        let errorEvent = null;
        await readNdjson(resp.body, (event) => {
          switch (event.type) {
            case "queued":
              setStatus("Queued...");
              break;
            case "waiting":
              setStatus("Waiting in queue (" + event.seconds_waited + "s)...");
              break;
            case "status":
              setStatus(STAGE_LABELS[event.stage] || event.message || "Working...");
              break;
            case "result":
              resultEvent = event;
              break;
            case "error":
              errorEvent = event;
              break;
          }
        });

        if (resultEvent) {
          const bytes = base64ToBytes(resultEvent.data_base64);
          const blob = new Blob([bytes], { type: resultEvent.content_type });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = resultEvent.filename || "output.epub";
          a.click();
          URL.revokeObjectURL(url);
          setStatus("Done! Your file is downloading.");
        } else if (errorEvent) {
          if (errorEvent.error_code) {
            showKnownError(errorEvent);
          } else {
            const parts = [errorEvent.error || "Conversion failed"];
            if (errorEvent.stdout) parts.push("stdout: " + errorEvent.stdout);
            if (errorEvent.stderr) parts.push("stderr: " + errorEvent.stderr);
            showPlainError(parts.join("\\n"));
          }
        } else {
          showPlainError("Conversion ended without a result.");
        }
      } catch (err) {
        showPlainError(err.name + ": " + err.message);
      }
    });

    async function readNdjson(body, onEvent) {
      const reader = body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\\n")) >= 0) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          if (!line) continue;
          eventLog.push(line);
          try { onEvent(JSON.parse(line)); } catch (_) {}
        }
      }
    }

    function base64ToBytes(b64) {
      const binary = atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return bytes;
    }

    function setStatus(msg) {
      status.textContent = msg;
      status.className = "";
      status.style.whiteSpace = "";
    }

    function showPlainError(msg) {
      status.className = "error";
      status.style.whiteSpace = "";
      status.innerHTML = "";
      const detail = document.createElement("div");
      detail.style.whiteSpace = "pre-wrap";
      detail.innerText = msg;
      status.appendChild(detail);
      // Plain errors carry no E_ code (timeouts, crashes, network failures,
      // "ended without a result"), so always offer the support contact.
      showLogControls(true);
    }

    // Append a "Show log" toggle (and, for unexpected errors, a Reddit support
    // line) below whatever error message is already in #status.
    function showLogControls(showSupport) {
      const controls = document.createElement("div");
      controls.className = "log-controls";

      if (showSupport) {
        const support = document.createElement("p");
        support.className = "support";
        support.innerHTML =
          'Still stuck? Did you read the error message and try again after a few minutes? Message <a href="https://www.reddit.com/user/chrisvariety" target="_blank" rel="noopener">u/chrisvariety</a> on Reddit for support, include the log below along with any info on the epub file you were trying to convert e.g. where you got it, what kind of book it is (for example, a novel or a textbook or a cookbook), and any other relevant details.';
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
        pre.textContent = eventLog.join("\\n");

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

    function showKnownError(err) {
      let html = '<div class="error-detail">';
      html += '<h2>' + esc(err.title) + '</h2>';
      html += '<code>' + esc(err.error_code) + '</code>';
      html += '<p>' + esc(err.description) + '</p>';
      if (err.solutions && err.solutions.length) {
        html += '<ul>';
        for (const s of err.solutions) {
          html += '<li><h3>' + esc(s.heading) + '</h3><p>' + esc(s.text) + '</p></li>';
        }
        html += '</ul>';
      }
      html += '</div>';
      status.innerHTML = html;
      status.className = "";
      // E_* codes are well-understood provider/account errors with self-service
      // fixes above, and transient:true marks upstream outages that clear on
      // their own — no need to send either group to Reddit. Everything else
      // (timeouts, crashes, rate limits, unknowns) is worth flagging to me.
      const isExpected =
        err.transient === true || (err.error_code && err.error_code.startsWith("E_"));
      showLogControls(!isExpected);
    }

    function esc(s) {
      const d = document.createElement("div");
      d.textContent = s;
      return d.innerHTML;
    }
  </script>
</body>
</html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/" && request.method === "GET") {
      return new Response(HTML, {
        headers: { "Content-Type": "text/html" },
      });
    }

    if (url.pathname === "/convert" && request.method === "POST") {
      const body = await request.arrayBuffer();

      if (!body.byteLength) {
        return Response.json({ error: "No file uploaded" }, { status: 400 });
      }

      // Transparent proxy to the converter. The container owns the full
      // credential lifecycle (it persists to Postgres directly), so the
      // Worker has nothing to inspect — it just relays the NDJSON stream.
      const upstream = await fetch(
        "https://acsm-converter-fly.fly.dev/convert",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/octet-stream",
            Authorization: `Bearer ${env.FLY_AUTH_TOKEN}`,
          },
          body,
        },
      );

      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: {
          "Content-Type":
            upstream.headers.get("Content-Type") || "application/x-ndjson",
        },
      });
    }

    return new Response("Not found", { status: 404 });
  },
};
