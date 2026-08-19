#!/usr/bin/env python3
"""HTTP server for ACSM-to-EPUB/PDF conversion using libgourou tools."""

import base64
import hashlib
import json
import os
import re
import shutil
import signal
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

# Set once a shutdown signal arrives: stop taking new work, let the conversion
# already in flight finish. Fly sends kill_signal (SIGINT by default) and
# SIGKILLs us kill_timeout later; without a handler Python dies instantly and
# takes acsmdownloader/adept_remove with it. That is not a recoverable failure
# -- a kill during fulfillment burns the single-use Adobe token (see the
# DOWNLOAD_TIMEOUT note below), permanently bricking that ACSM. So we drain.
_draining = threading.Event()
_server = None

ADEPT_DIR = "/home/libgourou/.adept"
WORK_DIR = "/home/libgourou/work"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

CRED_FILES = ("device.xml", "activation.xml", "devicesalt")

# Hard ceilings so a stalled provider connection can't hang the request
# forever (which holds the convert lock and silently times out at the CDN).
ACTIVATE_TIMEOUT = 90    # adept_activate: a couple of Adobe round-trips
# acsmdownloader: fulfill the loan AND fetch the (DRM'd) book over the net.
# Generous on purpose. Tripping this is not a recoverable "try again" -- fulfill()
# runs before download() in one process (vendor/libgourou/utils/acsmdownloader.cpp:85),
# the FulfillmentItem lives only in memory, and --resume only affects the file
# write. So a kill during download consumes a single-use fulfillment token and
# every retry goes back through /Fulfill, which some operators refuse with
# E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER -- permanently bricking that ACSM.
# Waiting on a slow provider is far cheaper than that, so err high.
DOWNLOAD_TIMEOUT = 600
# adept_remove: local crypto, no network, but time scales with book size on a
# single shared vCPU. Raised alongside DOWNLOAD_TIMEOUT: the large books that
# used to die during download now reach this stage, so a tight ceiling here
# would just move the same failure one step later -- and by this point the
# fulfillment token is already spent, so timing out is equally unrecoverable.
DECRYPT_TIMEOUT = 480
# How long to wait for an in-flight conversion at shutdown. Kept just under
# kill_timeout in fly.toml -- Fly SIGKILLs us at that deadline regardless, so
# leave a little room to log and exit cleanly. Conversions longer than this
# still lose (worst case is DOWNLOAD_TIMEOUT + DECRYPT_TIMEOUT, well past any
# allowed kill_timeout), but it covers the large majority of them.
DRAIN_TIMEOUT = 285
# Grace after the lock frees, so the last of the result body reaches the client
# before the interpreter exits and kills the (daemon) handler thread.
DRAIN_GRACE = 2
# How often to emit a keep-alive status event during a long-running step, so
# the CDN doesn't sever the (otherwise silent) connection mid-download.
HEARTBEAT_INTERVAL = 15
# Read size when streaming the result to the client. A multiple of 3 so each
# chunk base64-encodes cleanly (padding only ever appears at the true end).
RESULT_CHUNK = 3 * 1024 * 1024


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

# Shared card for upstream 5xx responses (libgourou exception 0x5011,
# CLIENT_HTTP_ERROR). Raised whenever Adobe's ACS (adeactivate.adobe.com) or the
# provider's distributor answers with a gateway/server error. libgourou retries
# curl-level failures 5x but returns HTTP >= 400 straight to us with no retry
# (vendor/libgourou/utils/drmprocessorclientimpl.cpp), so one bad gateway kills
# the whole run. Nothing about the user's file or account is at fault.
UPSTREAM_UNAVAILABLE = {
    # Nothing for me to debug and nothing the user can do but wait, so the
    # frontend suppresses the "message me on Reddit" prompt for this card.
    "transient": True,
    "title": "Adobe's DRM servers are temporarily unavailable",
    "description": (
        "The converter got a server error (HTTP 5xx) from Adobe or your "
        "content provider. This is an outage on their side -- there is "
        "nothing wrong with your ACSM file or your account, and it clears "
        "on its own once their servers recover."
    ),
    "solutions": [
        {
            "heading": "Wait and try again",
            "text": (
                "These outages are usually short-lived. Wait 10-15 minutes "
                "and convert the same file again -- no need to download a "
                "new one."
            ),
        },
        {
            "heading": "If it persists for hours",
            "text": (
                "A longer Adobe outage affects every ACSM tool, not just "
                "this one. Nothing on your end will fix it; try again "
                "later in the day."
            ),
        },
    ],
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
    # Adobe/provider-side outages. Separate keys because _match_known_error does
    # plain substring matching; they all share one card.
    "HTTP Error code 500": UPSTREAM_UNAVAILABLE,
    "HTTP Error code 502": UPSTREAM_UNAVAILABLE,
    "HTTP Error code 503": UPSTREAM_UNAVAILABLE,
    "HTTP Error code 504": UPSTREAM_UNAVAILABLE,
    # libgourou's curl client (CURLE_OPERATION_TIMEDOUT, exception code 0x500b):
    # acsmdownloader exits rc=1 on its own with this text in stdout, so it
    # surfaces as a CalledProcessError, NOT our wrapper's TimeoutExpired.
    # drmprocessorclientimpl.cpp sets no CURLOPT_TIMEOUT/CONNECTTIMEOUT, so curl
    # only raises this on its ~300s connect/DNS default -- never on a slow but
    # progressing transfer, which runs until our ceiling. Since DOWNLOAD_TIMEOUT
    # is now 600s, curl wins that race for connection stalls (it used to lose to
    # the old 180s). See TIMEOUT_GUIDANCE["acsmdownloader"] for the card shown
    # when our ceiling fires instead, i.e. a genuinely slow transfer.
    "Timeout was reached": {
        "title": "The download timed out",
        "description": (
            "The converter reached your content provider's server, but the "
            "download didn't finish in time. This usually means the provider's "
            "server is slow, overloaded, or temporarily unreachable -- it's "
            "almost always a short-lived problem on their end, not with your file."
        ),
        "solutions": [
            {
                "heading": "Wait a few minutes and try again",
                "text": (
                    "Provider slowdowns are usually temporary. Wait a few "
                    "minutes and convert the same file again."
                ),
            },
            {
                "heading": "Try a fresh ACSM file",
                "text": (
                    "If it keeps timing out, download a new ACSM file from your "
                    "content provider and try converting that instead."
                ),
            },
        ],
    },
}


# Friendly cards for our own wrapper timeouts (subprocess.TimeoutExpired —
# emitted when _run kills a hung child after its per-step ceiling). Keyed by
# command basename. Each entry mirrors the KNOWN_ERRORS shape so the frontend's
# showKnownError renders it as a structured card rather than a raw blob.
TIMEOUT_GUIDANCE = {
    "acsmdownloader": {
        "error_code": "Download timeout",
        "title": "The download timed out",
        "description": (
            "The converter took too long downloading your book from the content "
            "provider. This usually means the provider's server is slow or "
            "temporarily unreachable -- almost always a short-lived problem on "
            "their end."
        ),
        "solutions": [
            {
                "heading": "Wait a few minutes and try again",
                "text": (
                    "Provider slowdowns are usually temporary. Wait a few "
                    "minutes and convert the same file again."
                ),
            },
            {
                "heading": "Try a fresh ACSM file",
                "text": (
                    "If it keeps timing out, download a new ACSM file from your "
                    "content provider and try converting that instead."
                ),
            },
        ],
    },
    "adept_remove": {
        "error_code": "Decryption timeout",
        "title": "DRM removal took too long",
        "description": (
            "The local DRM-removal step ran past its time budget. This step "
            "doesn't touch the network -- it usually means the file is unusually "
            "large or the converter is under heavy load."
        ),
        "solutions": [
            {
                "heading": "Try again",
                "text": (
                    "Wait a moment and convert the same file again. If the "
                    "server was busy, the next attempt often succeeds."
                ),
            },
        ],
    },
    "adept_activate": {
        "error_code": "Activation timeout",
        "title": "Account activation timed out",
        "description": (
            "The converter couldn't activate a new anonymous Adobe account in "
            "time. This is a one-time setup step talking to Adobe and usually "
            "means Adobe's activation service is slow or temporarily unreachable."
        ),
        "solutions": [
            {
                "heading": "Try again",
                "text": "Wait a few minutes and try converting again.",
            },
        ],
    },
    # Fallback when a future _run call gets its own heartbeat_stage and timeout
    # but doesn't have a tailored card here.
    "_default": {
        "error_code": "Operation timeout",
        "title": "An internal step timed out",
        "description": (
            "Part of the conversion took too long and was aborted. This is "
            "usually a transient issue."
        ),
        "solutions": [
            {
                "heading": "Try again",
                "text": "Wait a moment and try converting the same file again.",
            },
        ],
    },
}

# Card for the silent-crash case: subprocess returncode < 0 means the child was
# killed by a signal (e.g. -9 SIGKILL on OOM, -11 SIGSEGV on segfault). These
# typically come back with empty stdout/stderr.
SIGNAL_GUIDANCE = {
    "title": "The converter crashed unexpectedly",
    "description": (
        "A part of the conversion was killed before it could finish -- usually "
        "a transient memory-pressure spike or an internal crash on an unusual "
        "file. Trying again often succeeds."
    ),
    "solutions": [
        {
            "heading": "Try again",
            "text": (
                "Wait a moment and convert the same file again. If it keeps "
                "failing on the same file, the file may be too large or unusual "
                "for the converter to handle right now."
            ),
        },
    ],
}


def _signal_name(returncode):
    """Name of the signal that killed a subprocess, or None for normal exits.

    `subprocess` reports `returncode = -N` when the child was terminated by
    signal N (e.g. -9 = SIGKILL, -11 = SIGSEGV). Lets the error event
    distinguish a crash/OOM-kill from a regular non-zero exit.
    """
    if returncode is None or returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"signal {-returncode}"


def _match_known_error(output):
    """Extract a known error code from command output, if present."""
    for code in KNOWN_ERRORS:
        if code in output:
            return code, KNOWN_ERRORS[code]
    return None, None


class ConvertHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            if _draining.is_set():
                # Fail the check so the proxy stops routing here while we drain.
                self._json_response(503, {"status": "draining"})
            else:
                self._json_response(200, {"status": "ok"})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/convert":
            self.send_response(404)
            self.end_headers()
            return

        if _draining.is_set():
            # Shutting down: refuse rather than start a fulfillment we cannot
            # finish, since a kill mid-download burns the ACSM. Under bluegreen
            # a fresh machine is already serving, so the client can just retry.
            self._json_response(503, {"error": "Server is shutting down; please retry"})
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
                          timeout=ACTIVATE_TIMEOUT, heartbeat_stage="activate")
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
            ], timeout=DECRYPT_TIMEOUT, heartbeat_stage="decrypt")

            content_type = (
                "application/epub+zip" if output_filename.lower().endswith(".epub")
                else "application/pdf"
            )
            out_size = os.path.getsize(decrypted_path)
            print(f"[convert] Success! Returning {output_filename} ({out_size} bytes)", flush=True)
            self._send_file_result(output_filename, content_type, decrypted_path)

        except subprocess.CalledProcessError as e:
            output = (e.stdout or "") + (e.stderr or "")
            label = os.path.basename(e.cmd[0]) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
            sig = _signal_name(e.returncode)
            sig_suffix = f" ({sig})" if sig else ""
            print(f"[convert] ERROR: {label} failed rc={e.returncode}{sig_suffix}", flush=True)
            print(f"[convert] stdout: {e.stdout}", flush=True)
            print(f"[convert] stderr: {e.stderr}", flush=True)

            error_code, known = _match_known_error(output)
            if known:
                self._send_event({"type": "error", "error_code": error_code, **known})
            elif e.returncode is not None and e.returncode < 0:
                # Killed by a signal — empty stdout/stderr is typical.
                # Render a structured "internal failure" card so the user gets
                # retry guidance instead of a raw blob, and include returncode/signal
                # for power users reading NDJSON.
                self._send_event({
                    "type": "error",
                    **SIGNAL_GUIDANCE,
                    "error_code": f"Internal failure ({sig or 'crash'})",
                    "command": label,
                    "returncode": e.returncode,
                })
            else:
                self._send_event({
                    "type": "error",
                    "error": f"{label} failed (exit {e.returncode})",
                    "command": label,
                    "returncode": e.returncode,
                    "stdout": e.stdout or "",
                    "stderr": e.stderr or "",
                })
        except subprocess.TimeoutExpired as e:
            label = os.path.basename(e.cmd[0]) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
            print(f"[convert] ERROR: {label} timed out after {e.timeout}s", flush=True)
            guidance = TIMEOUT_GUIDANCE.get(label, TIMEOUT_GUIDANCE["_default"])
            self._safe_terminal_event({
                "type": "error",
                **guidance,
                "command": label,
                "timeout_seconds": e.timeout,
            })
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

    def _send_file_result(self, filename, content_type, path):
        """Stream the terminal result event, base64-encoding the file from disk
        in chunks so we never hold the whole (100+ MB) payload in memory.

        The wire format is unchanged: a single NDJSON line holding a JSON object
        with a `data_base64` field. We let json.dumps build (and escape) the
        envelope with an empty payload, then split it just inside the opening
        quote of data_base64 and stream the base64 into the gap. base64 emits no
        newlines, so the line stays valid NDJSON and the client parser is
        untouched.
        """
        self._terminal_sent = True
        envelope = json.dumps({
            "type": "result",
            "filename": filename,
            "content_type": content_type,
            "data_base64": "",
        })
        # Empty payload renders as `…"data_base64": ""}`. Drop the closing `"}`
        # to leave the opening quote open, stream the value, then close it.
        assert envelope.endswith('""}'), envelope
        self.wfile.write(envelope[:-2].encode())
        with open(path, "rb") as f:
            while True:
                chunk = f.read(RESULT_CHUNK)
                if not chunk:
                    break
                self.wfile.write(base64.b64encode(chunk))
        self.wfile.write(b'"}\n')
        self.wfile.flush()

    def _safe_terminal_event(self, event):
        """Emit a terminal event, swallowing write failures."""
        try:
            self._send_event(event)
        except Exception:
            pass

    def _safe_terminal_error(self, message, **extra):
        """Emit a terminal error event, swallowing write failures."""
        self._safe_terminal_event({"type": "error", "error": message, **extra})

    def _json_response(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _handle_shutdown(signum, _frame):
    """Start draining. Runs on the main thread, interrupting serve_forever()."""
    if _draining.is_set():
        return  # already draining; ignore repeats
    _draining.set()
    print(f"[drain] {_signal_name(-signum)} received; refusing new conversions",
          flush=True)
    # Keep serving while we drain: /health now answers 503 and /convert is
    # refused, so the proxy sees us go unhealthy promptly. Stopping the accept
    # loop here instead would leave both endpoints hanging until the socket
    # closed, which reads as a timeout rather than a clean "not me". The drain
    # runs off-thread because shutdown() must not be called from the thread
    # running serve_forever() -- it waits for that loop to exit.
    threading.Thread(target=_drain_then_stop, daemon=True).start()


def _drain_then_stop():
    """Let the in-flight conversion finish, then stop the accept loop."""
    _drain()
    _server.shutdown()


def _drain():
    """Wait for the in-flight conversion, if any, to finish.

    Acquiring the lock means no conversion is running. Handler threads are
    daemons, so exiting the interpreter would kill a running acsmdownloader
    mid-fulfillment -- exactly the unrecoverable case we are avoiding.
    """
    start = time.monotonic()
    if _convert_lock.acquire(timeout=DRAIN_TIMEOUT):
        _convert_lock.release()
        print(f"[drain] idle after {time.monotonic() - start:.0f}s; exiting",
              flush=True)
        time.sleep(DRAIN_GRACE)
    else:
        print(f"[drain] conversion still in flight after {DRAIN_TIMEOUT}s; "
              f"exiting anyway before Fly SIGKILLs us", flush=True)


if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(ADEPT_DIR, exist_ok=True)
    _server = ThreadingHTTPServer(("0.0.0.0", 8080), ConvertHandler)
    # Fly's default kill_signal is SIGINT; take SIGTERM too so the drain runs
    # however we're stopped.
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    print("Server listening on port 8080", flush=True)
    _server.serve_forever()  # returns once _drain_then_stop calls shutdown()
    print("[drain] accept loop stopped; exiting", flush=True)
