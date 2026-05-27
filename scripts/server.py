#!/usr/bin/env python3
"""HTTP server for ACSM-to-EPUB/PDF conversion using libgourou tools."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from contextlib import closing
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

try:
    import psycopg2
except ImportError:  # caching is optional; server still runs without it
    psycopg2 = None

_convert_lock = threading.Lock()

ADEPT_DIR = "/home/libgourou/.adept"
WORK_DIR = "/home/libgourou/work"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

CRED_FILES = ("device.xml", "activation.xml", "devicesalt")

# Hard ceilings so a stalled provider connection can't hang the request
# forever (which holds the convert lock and silently times out at the CDN).
ACTIVATE_TIMEOUT = 90    # adept_activate: a couple of Adobe round-trips
DOWNLOAD_TIMEOUT = 180   # acsmdownloader: fetch the (DRM'd) book over the net
DECRYPT_TIMEOUT = 90     # adept_remove: local crypto, no network
# How often to emit a keep-alive status event during a long-running step, so
# the CDN doesn't sever the (otherwise silent) connection mid-download.
HEARTBEAT_INTERVAL = 15


def _extract_user_id(acsm_bytes):
    """Pull the Adobe <userId> out of an ACSM file, if present."""
    match = re.search(rb"<userId>([^<]+)</userId>", acsm_bytes)
    return match.group(1).strip().decode("ascii", "ignore") if match else None


def _cache_key(acsm_bytes):
    """Stable per-fulfillment credential cache key, with the reason for logs.

    Priority:
      1. <userId>      Adobe-account files (e.g. Google Play). Kept bare so it
                       still matches seeded rows and groups a user's loans onto
                       one device (respecting providers' per-user device limits).
      2. <transaction> Fulfillment tokens that carry no userId (e.g. OverDrive).
                       Stable within a given ACSM and unique per fulfillment, so
                       a retry reuses the device that already holds the loan
                       instead of minting a new one (which the provider rejects
                       with E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER).
      3. sha256(acsm)  Last resort when neither tag is present — still stable
                       per file, so retries stay idempotent without pinning
                       everything to one shared device.
    """
    user_id = _extract_user_id(acsm_bytes)
    if user_id:
        return user_id, "userId"
    match = re.search(rb"<transaction>([^<]+)</transaction>", acsm_bytes)
    if match:
        return "txn:" + match.group(1).strip().decode("ascii", "ignore"), "transaction"
    return "acsm:" + hashlib.sha256(acsm_bytes).hexdigest(), "sha256"


def _caching_enabled():
    return bool(psycopg2 and DATABASE_URL)


def load_cached_creds(cache_key):
    """Return {filename: bytes} for a cached anonymous device, or None."""
    if not (_caching_enabled() and cache_key):
        return None
    try:
        with closing(psycopg2.connect(DATABASE_URL)) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT device_xml, activation_xml, devicesalt "
                "FROM adept_credentials WHERE id = %s",
                (cache_key,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return dict(zip(CRED_FILES, (bytes(col) for col in row)))
    except Exception as e:
        print(f"[creds] cache read failed: {e}", flush=True)
        return None


def save_cached_creds(cache_key, creds):
    """Persist freshly activated creds. Best-effort; never raises."""
    if not (_caching_enabled() and cache_key):
        return
    try:
        with closing(psycopg2.connect(DATABASE_URL)) as conn:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO adept_credentials "
                    "(id, device_xml, activation_xml, devicesalt, created_at) "
                    "VALUES (%s, %s, %s, %s, NOW()) "
                    "ON CONFLICT (id) DO NOTHING",
                    (
                        cache_key,
                        psycopg2.Binary(creds["device.xml"]),
                        psycopg2.Binary(creds["activation.xml"]),
                        psycopg2.Binary(creds["devicesalt"]),
                    ),
                )
        print(f"[creds] cached credentials for {cache_key}", flush=True)
    except Exception as e:
        print(f"[creds] cache write failed: {e}", flush=True)

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
        self._terminal_sent = False
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
            # The client interprets a stream that closes with no result/error
            # as "Conversion ended without a result." Guarantee a terminal event.
            if not self._terminal_sent:
                print("[convert] No terminal event sent; emitting fallback error", flush=True)
                self._safe_terminal_error("Conversion ended unexpectedly without a result")

    def _do_convert(self, body):
        work_dir = tempfile.mkdtemp(dir=WORK_DIR)
        try:
            acsm_path = os.path.join(work_dir, "input.acsm")
            with open(acsm_path, "wb") as f:
                f.write(body)

            if os.path.exists(ADEPT_DIR):
                shutil.rmtree(ADEPT_DIR)

            # Credentials are cached per Adobe userId so we don't re-activate a
            # fresh anonymous device on every request (which burns device slots
            # and trips provider device limits). The container owns this cache
            # directly; nothing credential-related travels over the response.
            cache_key, key_kind = _cache_key(body)
            print(f"[convert] cache_key={cache_key!r} (from {key_kind}) "
                  f"caching={_caching_enabled()}", flush=True)
            cached = load_cached_creds(cache_key)
            if cached:
                print(f"[convert] credential cache HIT for {cache_key}", flush=True)
                self._send_event({"type": "status", "stage": "hydrate",
                                  "message": "Loading cached credentials"})
                os.makedirs(ADEPT_DIR, exist_ok=True)
                for name, data in cached.items():
                    with open(os.path.join(ADEPT_DIR, name), "wb") as f:
                        f.write(data)
            else:
                print(f"[convert] credential cache MISS for {cache_key}; "
                      "activating a new anonymous device", flush=True)
                self._send_event({"type": "status", "stage": "activate",
                                  "message": "Activating anonymous Adept account"})
                self._run(["adept_activate", "--anonymous", "--output-dir", ADEPT_DIR],
                          timeout=ACTIVATE_TIMEOUT)
                # Persist immediately, before the risky download — so a fresh
                # activation survives a later download/decrypt failure and a
                # retry reuses the device that already holds the loan.
                fresh = {}
                for name in CRED_FILES:
                    with open(os.path.join(ADEPT_DIR, name), "rb") as f:
                        fresh[name] = f.read()
                save_cached_creds(cache_key, fresh)

            self._send_event({"type": "status", "stage": "download",
                              "message": "Downloading encrypted file"})
            print("[convert] starting download (acsmdownloader)", flush=True)
            result = self._run(
                ["acsmdownloader", "--adept-directory", ADEPT_DIR, acsm_path],
                cwd=work_dir,
                timeout=DOWNLOAD_TIMEOUT,
                heartbeat_stage="download",
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
            enc_size = os.path.getsize(encrypted_path) if os.path.exists(encrypted_path) else -1
            print(f"[convert] downloaded {output_filename!r} ({enc_size} bytes); removing DRM",
                  flush=True)

            self._send_event({"type": "status", "stage": "decrypt",
                              "message": "Removing DRM"})
            self._run([
                "adept_remove",
                "--adept-directory", ADEPT_DIR,
                "--output-file", decrypted_path,
                encrypted_path,
            ], timeout=DECRYPT_TIMEOUT)

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
        except subprocess.TimeoutExpired as e:
            label = os.path.basename(e.cmd[0]) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
            print(f"[convert] ERROR: {label} timed out after {e.timeout}s", flush=True)
            self._safe_terminal_error(
                "The download timed out. The content provider's server may be "
                "slow or unreachable right now — please try again in a few minutes."
            )
        except (BrokenPipeError, ConnectionResetError):
            raise  # client went away; do_POST logs it, nothing to report
        except Exception:
            # Anything else (e.g. a missing decrypted file) would otherwise
            # propagate and close the socket silently — surface it instead.
            print("[convert] ERROR: Unexpected exception during conversion:", flush=True)
            traceback.print_exc()
            self._safe_terminal_error("Unexpected error during conversion")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _run(self, cmd, *, timeout=None, heartbeat_stage=None, **kwargs):
        """Run a command, logging timing and output.

        Enforces `timeout` (raising subprocess.TimeoutExpired and killing the
        child) so a stalled network call can't hang the request forever. When
        `heartbeat_stage` is set, emits a periodic status event while the
        command runs so the CDN doesn't sever an otherwise-silent connection.
        """
        label = os.path.basename(cmd[0])
        print(f"[run] $ {' '.join(cmd)} (timeout={timeout}s)", flush=True)
        start = time.monotonic()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs
        )
        last_beat = start
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=2)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                elapsed = now - start
                if timeout is not None and elapsed >= timeout:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    print(f"[run] {label} TIMED OUT after {elapsed:.0f}s", flush=True)
                    self._log_output(label, stdout, stderr)
                    raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
                if heartbeat_stage and now - last_beat >= HEARTBEAT_INTERVAL:
                    print(f"[run] {label} still running after {elapsed:.0f}s", flush=True)
                    try:
                        self._send_event({"type": "status", "stage": heartbeat_stage,
                                          "message": f"Still working ({int(elapsed)}s)"})
                    except (BrokenPipeError, ConnectionResetError):
                        # Client/CDN went away — stop wasting work on a dead socket.
                        print(f"[run] {label} client disconnected; killing", flush=True)
                        proc.kill()
                        proc.communicate()
                        raise
                    last_beat = now

        elapsed = time.monotonic() - start
        rc = proc.returncode
        print(f"[run] {label} exited rc={rc} in {elapsed:.1f}s "
              f"(stdout={len(stdout)}b stderr={len(stderr)}b)", flush=True)
        self._log_output(label, stdout, stderr)
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

    @staticmethod
    def _log_output(label, stdout, stderr):
        if stdout and stdout.strip():
            print(f"[run] {label} stdout: {stdout.strip()[:2000]}", flush=True)
        if stderr and stderr.strip():
            print(f"[run] {label} stderr: {stderr.strip()[:2000]}", flush=True)

    def _send_event(self, obj):
        if obj.get("type") in ("result", "error"):
            self._terminal_sent = True
        line = json.dumps(obj).encode() + b"\n"
        self.wfile.write(line)
        self.wfile.flush()

    def _safe_terminal_error(self, message, **extra):
        """Emit a terminal error event, swallowing write failures."""
        try:
            self._send_event({"type": "error", "error": message, **extra})
        except Exception:
            pass

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
