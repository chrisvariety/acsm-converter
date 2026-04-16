#!/usr/bin/env python3
"""HTTP server for ACSM-to-EPUB/PDF conversion using libgourou tools."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler

ADEPT_DIR = "/home/libgourou/.adept"
WORK_DIR = "/home/libgourou/work"

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

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._json_response(400, {"error": "Empty request body"})
            return

        body = self.rfile.read(content_length)
        filename = self.headers.get("X-Filename", "input.acsm")
        print(f"[convert] Received {filename} ({content_length} bytes)", flush=True)

        work_dir = tempfile.mkdtemp(dir=WORK_DIR)
        try:
            acsm_path = os.path.join(work_dir, filename)
            with open(acsm_path, "wb") as f:
                f.write(body)

            # Activate anonymous credentials if not already present
            if not os.path.exists(os.path.join(ADEPT_DIR, "device.xml")):
                # --output-dir requires the directory to not exist yet
                if os.path.exists(ADEPT_DIR):
                    shutil.rmtree(ADEPT_DIR)
                print("[convert] Running adept_activate --anonymous", flush=True)
                self._run(["adept_activate", "--anonymous", "--output-dir", ADEPT_DIR])
                print("[convert] Activation complete", flush=True)
            else:
                print("[convert] Credentials already exist, skipping activation", flush=True)

            # Download the encrypted file from the ACSM link
            print("[convert] Running acsmdownloader", flush=True)
            result = self._run(
                ["acsmdownloader", "--adept-directory", ADEPT_DIR, acsm_path],
                cwd=work_dir,
            )
            print(f"[convert] acsmdownloader stdout: {result.stdout}", flush=True)
            print(f"[convert] acsmdownloader stderr: {result.stderr}", flush=True)

            # Parse output to find the downloaded filename.
            # acsmdownloader outputs lines like "Created Dire Bound.epub"
            # where the first word is a status prefix — match the original
            # entrypoint.sh approach: grep for epub/pdf, drop the first word.
            output_filename = None
            for line in (result.stdout + result.stderr).splitlines():
                if re.search(r"\.(epub|pdf)\b", line, re.IGNORECASE):
                    parts = line.strip().split(" ", 1)
                    if len(parts) == 2:
                        output_filename = parts[1].strip()
                    else:
                        output_filename = parts[0].strip()
                    break

            if not output_filename:
                print(f"[convert] ERROR: Could not determine output filename", flush=True)
                self._json_response(500, {
                    "error": "Could not determine output filename",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                })
                return

            print(f"[convert] Output file: {output_filename}", flush=True)
            encrypted_path = os.path.join(work_dir, output_filename)
            decrypted_path = os.path.join(work_dir, "decrypted_" + output_filename)

            # Remove DRM
            print("[convert] Running adept_remove", flush=True)
            self._run([
                "adept_remove",
                "--adept-directory", ADEPT_DIR,
                "--output-file", decrypted_path,
                encrypted_path,
            ])

            # Send the decrypted file back
            with open(decrypted_path, "rb") as f:
                data = f.read()

            print(f"[convert] Success! Returning {output_filename} ({len(data)} bytes)", flush=True)
            content_type = (
                "application/epub+zip" if output_filename.endswith(".epub")
                else "application/pdf"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{output_filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        except subprocess.CalledProcessError as e:
            output = (e.stdout or "") + (e.stderr or "")
            print(f"[convert] ERROR: Command failed: {e.cmd}", flush=True)
            print(f"[convert] stdout: {e.stdout}", flush=True)
            print(f"[convert] stderr: {e.stderr}", flush=True)

            error_code, known = _match_known_error(output)
            if known:
                print(f"[convert] Matched known error: {error_code}", flush=True)
                self._json_response(400, {
                    "error_code": error_code,
                    **known,
                })
            else:
                self._json_response(500, {
                    "error": f"Command failed: {e.cmd}",
                    "stdout": e.stdout or "",
                    "stderr": e.stderr or "",
                })
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _run(self, cmd, **kwargs):
        return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)

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
    server = HTTPServer(("0.0.0.0", 8080), ConvertHandler)
    print("Server listening on port 8080")
    server.serve_forever()
