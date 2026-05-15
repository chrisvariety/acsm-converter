import { Container, getContainer } from "@cloudflare/containers";

export class MyContainer extends Container {
  defaultPort = 8080;
}

function extractAdeptId(acsmBytes) {
  const text = new TextDecoder().decode(acsmBytes);
  const match = text.match(/<userId>([^<]+)<\/userId>/);
  return match ? match[1].trim() : null;
}

function bytesToBase64(value) {
  const bytes = value instanceof ArrayBuffer ? new Uint8Array(value) : value;
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

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
      status.innerText = msg;
      status.className = "error";
      status.style.whiteSpace = "pre-wrap";
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
  async fetch(request, env, ctx) {
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

      const id = extractAdeptId(body);

      const containerHeaders = { "Content-Type": "application/octet-stream" };
      if (id) {
        const cached = await env.ADEPT_DB.prepare(
          "SELECT device_xml, activation_xml, devicesalt FROM adept_credentials WHERE id = ?"
        ).bind(id).first();
        if (cached) {
          containerHeaders["X-Adept-Device-Xml"] = bytesToBase64(cached.device_xml);
          containerHeaders["X-Adept-Activation-Xml"] = bytesToBase64(cached.activation_xml);
          containerHeaders["X-Adept-Device-Salt"] = bytesToBase64(cached.devicesalt);
        }
      }

      const upstream = await fetch("https://acsm-converter-fly.fly.dev/convert", {
        method: "POST",
        headers: { ...containerHeaders, Authorization: `Bearer ${env.FLY_AUTH_TOKEN}` },
        body,
      });

      // Pre-stream failure (e.g. 401, 400) — surface as-is.
      if (!upstream.ok) {
        return new Response(upstream.body, {
          status: upstream.status,
          statusText: upstream.statusText,
          headers: upstream.headers,
        });
      }

      // Pipe the NDJSON stream to the browser. Intercept "credentials"
      // events along the way and persist them to D1 via ctx.waitUntil.
      const { readable, writable } = new TransformStream();
      const upstreamReader = upstream.body.getReader();
      const writer = writable.getWriter();
      const decoder = new TextDecoder();
      const encoder = new TextEncoder();

      const forward = (async () => {
        let buffer = "";
        try {
          while (true) {
            const { done, value } = await upstreamReader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buffer.indexOf("\n")) >= 0) {
              const line = buffer.slice(0, idx);
              buffer = buffer.slice(idx + 1);
              if (!line.trim()) continue;
              let event;
              try { event = JSON.parse(line); } catch { continue; }

              if (event.type === "credentials") {
                if (id) {
                  ctx.waitUntil(
                    env.ADEPT_DB.prepare(
                      "INSERT OR IGNORE INTO adept_credentials (id, device_xml, activation_xml, devicesalt, created_at) VALUES (?, ?, ?, ?, ?)"
                    ).bind(
                      id,
                      base64ToBytes(event["X-Adept-Device-Xml"]),
                      base64ToBytes(event["X-Adept-Activation-Xml"]),
                      base64ToBytes(event["X-Adept-Device-Salt"]),
                      Math.floor(Date.now() / 1000)
                    ).run().catch((e) => console.error("Failed to persist credentials:", e))
                  );
                }
                continue; // never forward credentials to the browser
              }

              await writer.write(encoder.encode(line + "\n"));
            }
          }
        } catch (err) {
          console.error("Stream forwarding error:", err);
        } finally {
          try { await writer.close(); } catch {}
        }
      })();

      ctx.waitUntil(forward);

      return new Response(readable, {
        status: 200,
        headers: { "Content-Type": "application/x-ndjson" },
      });
    }

    return new Response("Not found", { status: 404 });
  },
};
