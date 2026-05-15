#!/usr/bin/env python3
"""HTTP server for ACSM-to-EPUB/PDF conversion using libgourou tools."""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

_convert_lock = threading.Lock()

ADEPT_DIR = "/home/libgourou/.adept"
WORK_DIR = "/home/libgourou/work"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")

CRED_FILES = ("device.xml", "activation.xml", "devicesalt")
CRED_HEADERS = {
    "device.xml": "X-Adept-Device-Xml",
    "activation.xml": "X-Adept-Activation-Xml",
    "devicesalt": "X-Adept-Device-Salt",
}

# Known ADEPT error codes and user-friendly guidance
KNOWN_ERRORS = {
    "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER": {
        "title": "This ACSM file has already been opened by another user",
        "description": (
            "This means the ACSM file was already converted or opened (fulfilled) "
            "by a different user account. This commonly happens if you previously "
            "opened it with Adobe Digital Editions, OverDrive, or another tool."
        ),
        "solutions": [
            {
                "heading": "Deauthorize Adobe Digital Editions",
                "text": (
                    "If you previously opened this file with Adobe Digital Editions, "
                    "deauthorize it: open ADE, log in with the same account you used "
                    "before, then go to Help > Erase Authorization."
                ),
            },
            {
                "heading": "Deauthorize OverDrive",
                "text": (
                    "If you previously opened this file in the OverDrive app, go to "
                    "the app's settings and deauthorize it."
                ),
            },
            {
                "heading": "Download a new ACSM file",
                "text": (
                    "In some cases, downloading a fresh ACSM file from your provider "
                    "can resolve this. Whether this works depends on your provider -- "
                    "for example, using a new account on Archive.org will give you a "
                    "new unfulfilled file."
                ),
            },
            {
                "heading": "Contact your content provider",
                "text": (
                    "If none of the above works, contact your content provider's "
                    "support (Archive.org, Kobo, Google, OverDrive, etc.) and ask "
                    "them to reset the authorization for your book."
                ),
            },
        ],
    },
    "E_GOOGLE_DEVICE_LIMIT_REACHED": {
        "title": "Google Play device limit reached",
        "description": (
            "This error is specific to Google Play books and means you've opened "
            "this ACSM file on too many devices according to Google's limit."
        ),
        "solutions": [
            {
                "heading": "Deauthorize a device",
                "text": (
                    "If you've used Adobe Digital Editions, OverDrive, or similar "
                    "software, try deauthorizing one of them to free up a device slot."
                ),
            },
            {
                "heading": "Contact Google Support",
                "text": (
                    "Many users have resolved this by contacting Google Support "
                    "directly and describing the issue."
                ),
            },
        ],
    },
    "E_ADEPT_REQUEST_EXPIRED": {
        "title": "ACSM file has expired",
        "description": (
            "ACSM files contain an expiration date. This error usually means too "
            "much time has passed since the file was downloaded, or that the clock "
            "on the device that originally opened it was misconfigured."
        ),
        "solutions": [
            {
                "heading": "Download a new ACSM file",
                "text": (
                    "Go back to your content provider and download a fresh copy "
                    "of the ACSM file. This usually resolves the issue."
                ),
            },
        ],
    },
    "E_LIC_LICENSE_SIGN_ERROR": {
        "title": "Provider-side signing error",
        "description": (
            "This means the content provider (e.g. Google, Kobo) is experiencing "
            "an issue on their end."
        ),
        "solutions": [
            {
                "heading": "Try again",
                "text": (
                    "This is sometimes a temporary issue. Try converting again "
                    "in a few minutes."
                ),
            },
            {
                "heading": "Contact your content provider",
                "text": (
                    "If the issue persists (it can sometimes take a few days to "
                    "resolve), contact your content provider and let them know "
                    "you're seeing this error."
                ),
            },
        ],
    },
    "HTTP Error code 429": {
        "title": "Rate limited by the content provider",
        "description": (
            "The content provider returned HTTP 429 (Too Many Requests). This is "
            "usually a short-lived rate limit applied to the server's IP address "
            "or to the specific book you're trying to download."
        ),
        "solutions": [
            {
                "heading": "Wait and try again",
                "text": (
                    "Rate limits typically clear in a few minutes. Wait 5-15 "
                    "minutes and try converting again."
                ),
            },
            {
                "heading": "Try a fresh ACSM file",
                "text": (
                    "If retrying doesn't help, download a new ACSM file from your "
                    "content provider -- the specific file may have hit a per-book "
                    "download limit."
                ),
            },
        ],
    },
}


def _match_known_error(output):
    """Extract a known error code from command output, if present."""
    for code in KNOWN_ERRORS:
        if code in output:
            return code, KNOWN_ERRORS[code]
    return None, None


class ConvertHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/convert":
            self.send_response(404)
            self.end_headers()
            return

        if AUTH_TOKEN:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {AUTH_TOKEN}":
                self._json_response(401, {"error": "Unauthorized"})
                return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_response(400, {"error": "Empty request body"})
            return

        body = self.rfile.read(content_length)
        print(f"[convert] Received {content_length} bytes", flush=True)

        # Start NDJSON streaming response. After this point, errors are
        # reported as {"type":"error",...} events, not via HTTP status.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            self._send_event({"type": "queued"})
            waited = 0
            while not _convert_lock.acquire(timeout=10):
                waited += 10
                self._send_event({"type": "waiting", "seconds_waited": waited})
        except (BrokenPipeError, ConnectionResetError):
            print("[convert] Client disconnected while queued", flush=True)
            return

        try:
            self._do_convert(body)
        except (BrokenPipeError, ConnectionResetError):
            print("[convert] Client disconnected during conversion", flush=True)
        finally:
            _convert_lock.release()

    def _do_convert(self, body):
        work_dir = tempfile.mkdtemp(dir=WORK_DIR)
        try:
            acsm_path = os.path.join(work_dir, "input.acsm")
            with open(acsm_path, "wb") as f:
                f.write(body)

            if os.path.exists(ADEPT_DIR):
                shutil.rmtree(ADEPT_DIR)

            provided = {
                name: self.headers.get(header)
                for name, header in CRED_HEADERS.items()
            }
            if all(provided.values()):
                self._send_event({"type": "status", "stage": "hydrate",
                                  "message": "Hydrating credentials from request headers"})
                os.makedirs(ADEPT_DIR, exist_ok=True)
                for name, b64 in provided.items():
                    with open(os.path.join(ADEPT_DIR, name), "wb") as f:
                        f.write(base64.b64decode(b64))
            else:
                self._send_event({"type": "status", "stage": "activate",
                                  "message": "Activating anonymous Adept account"})
                self._run(["adept_activate", "--anonymous", "--output-dir", ADEPT_DIR])
                # Emit fresh creds immediately so the Worker can persist them
                # even if the rest of the request fails.
                creds_event = {"type": "credentials"}
                for name in CRED_FILES:
                    with open(os.path.join(ADEPT_DIR, name), "rb") as f:
                        creds_event[CRED_HEADERS[name]] = base64.b64encode(f.read()).decode()
                self._send_event(creds_event)

            self._send_event({"type": "status", "stage": "download",
                              "message": "Downloading encrypted file"})
            result = self._run(
                ["acsmdownloader", "--adept-directory", ADEPT_DIR, acsm_path],
                cwd=work_dir,
            )

            # acsmdownloader prints lines like "Created File Name.epub" — first
            # token is a status prefix, remainder is the filename.
            output_filename = None
            for line in (result.stdout + result.stderr).splitlines():
                if re.search(r"\.(epub|pdf)\b", line, re.IGNORECASE):
                    parts = line.strip().split(" ", 1)
                    output_filename = parts[1].strip() if len(parts) == 2 else parts[0].strip()
                    break

            if not output_filename:
                self._send_event({
                    "type": "error",
                    "error": "Could not determine output filename",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                })
                return

            encrypted_path = os.path.join(work_dir, output_filename)
            decrypted_path = os.path.join(work_dir, "decrypted_" + output_filename)

            self._send_event({"type": "status", "stage": "decrypt",
                              "message": "Removing DRM"})
            self._run([
                "adept_remove",
                "--adept-directory", ADEPT_DIR,
                "--output-file", decrypted_path,
                encrypted_path,
            ])

            with open(decrypted_path, "rb") as f:
                data = f.read()

            content_type = (
                "application/epub+zip" if output_filename.lower().endswith(".epub")
                else "application/pdf"
            )
            print(f"[convert] Success! Returning {output_filename} ({len(data)} bytes)", flush=True)
            self._send_event({
                "type": "result",
                "filename": output_filename,
                "content_type": content_type,
                "data_base64": base64.b64encode(data).decode(),
            })

        except subprocess.CalledProcessError as e:
            output = (e.stdout or "") + (e.stderr or "")
            print(f"[convert] ERROR: Command failed: {e.cmd}", flush=True)
            print(f"[convert] stdout: {e.stdout}", flush=True)
            print(f"[convert] stderr: {e.stderr}", flush=True)

            error_code, known = _match_known_error(output)
            if known:
                self._send_event({"type": "error", "error_code": error_code, **known})
            else:
                self._send_event({
                    "type": "error",
                    "error": f"Command failed: {e.cmd}",
                    "stdout": e.stdout or "",
                    "stderr": e.stderr or "",
                })
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _run(self, cmd, **kwargs):
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)

    def _send_event(self, obj):
        line = json.dumps(obj).encode() + b"\n"
        self.wfile.write(line)
        self.wfile.flush()

    def _json_response(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(ADEPT_DIR, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", 8080), ConvertHandler)
    print("Server listening on port 8080")
    server.serve_forever()
