import { Container, getContainer } from "@cloudflare/containers";

export class MyContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "10m";
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
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = form.file.files[0];
      if (!file) return;
      status.textContent = "Converting... this may take a minute.";
      status.className = "";
      try {
        const resp = await fetch("/convert", {
          method: "POST",
          headers: { "X-Filename": file.name },
          body: await file.arrayBuffer(),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => null);
          if (err && err.error_code) {
            showKnownError(err);
          } else if (err) {
            const parts = [err.error || "Conversion failed"];
            if (err.stdout) parts.push("stdout: " + err.stdout);
            if (err.stderr) parts.push("stderr: " + err.stderr);
            showPlainError(parts.join("\\n"));
          } else {
            showPlainError("Conversion failed: " + resp.statusText);
          }
          return;
        }
        const disposition = resp.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename="(.+?)"/);
        const filename = match ? match[1] : "output.epub";
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
        status.textContent = "Done! Your file is downloading.";
        status.className = "";
      } catch (err) {
        showPlainError(err.message);
      }
    });

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
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/" && request.method === "GET") {
      return new Response(HTML, {
        headers: { "Content-Type": "text/html" },
      });
    }

    if (url.pathname === "/convert" && request.method === "POST") {
      const filename = request.headers.get("X-Filename") || "input.acsm";
      const body = await request.arrayBuffer();

      if (!body.byteLength) {
        return Response.json({ error: "No file uploaded" }, { status: 400 });
      }

      const container = getContainer(env.MY_CONTAINER, "default");
      return container.fetch("http://container/convert", {
        method: "POST",
        headers: {
          "X-Filename": filename,
          "Content-Type": "application/octet-stream",
        },
        body,
      });
    }

    return new Response("Not found", { status: 404 });
  },
};
