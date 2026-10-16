#!/usr/bin/env python3
"""Build Ledger server — stores only encrypted data.

Endpoints:
  GET    api/ping
  GET    api/kdf?user=        -> {salt, iter} for client-side key derivation
  POST   api/register         -> {user, invite_code}  create account
  GET    api/vault            -> {v, salt, iter, pw, rs, iv, ct}  (with X-User, X-Auth)
  PUT    api/vault            -> {baseRev} body {vault json}  (409 on revision mismatch)
  GET    api/doc/<id>         -> document metadata + encrypted file bytes
  PUT    api/doc/<id>         -> encrypted document bytes
  DELETE api/doc/<id>
  GET    api/blob/<id>        -> encrypted blob bytes
  PUT    api/blob/<id>        -> encrypted blob bytes
  DELETE api/blob/<id>
  DELETE api/account

Auth headers: X-User, X-Auth (X-Auth is the hashed login credential from kekAuth)

Data dir layout:
  DATA_DIR/<user>/vault.json     — opaque encrypted vault + revision counter
  DATA_DIR/<user>/docs/<id>/     — document files (.enc)
  DATA_DIR/<user>/blobs/<id>     — inline document blobs

Never stores: passwords, recovery keys, DEK, or readable data.
"""

import os
import sys
import json
import hashlib
import secrets
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DATA_DIR = os.environ.get("DATA_DIR", "./data")
DOCS_DIR = os.environ.get("DOCS_DIR", "./docs")
WEB_DIR = os.environ.get("WEB_DIR", ".")
PORT = int(os.environ.get("PORT", "8099"))

# Invite code and user cap (set via env, or from Home Assistant add-on options)
INVITE_CODE = os.environ.get("INVITE_CODE", "")
USER_CAP = int(os.environ.get("USER_CAP", "0"))  # 0 = unlimited

# Per-user failed attempt tracking: {user: {"n": int, "until": float}}
_attempts = {}

# In-memory revision tracking (persisted inside vault.json)
# Each vault.json stores: {v, salt, iter, pw, rs, iv, ct, rev}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)


def user_dir(user):
    d = os.path.join(DATA_DIR, user)
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "docs"), exist_ok=True)
    os.makedirs(os.path.join(d, "blobs"), exist_ok=True)
    return d


def vault_path(user):
    return os.path.join(user_dir(user), "vault.json")


def load_vault(user):
    p = vault_path(user)
    if not os.path.exists(p):
        return None
    with open(p, "r") as f:
        return json.load(f)


def save_vault(user, data):
    p = vault_path(user)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, p)


def doc_path(user, doc_id):
    return os.path.join(user_dir(user), "docs", doc_id)


def blob_path(user, blob_id):
    return os.path.join(user_dir(user), "blobs", blob_id)


def check_attempts(user):
    """Return (allowed, remaining_seconds). If not allowed, seconds to wait."""
    now = time.time()
    a = _attempts.get(user, {"n": 0, "until": 0})
    if a["until"] > now:
        return False, int(a["until"] - now)
    return True, 0


def record_failure(user):
    now = time.time()
    a = _attempts.get(user, {"n": 0, "until": 0})
    a["n"] += 1
    if a["n"] >= 5:
        a["until"] = now + min(3600, 30 * (2 ** (a["n"] - 5)))
    _attempts[user] = a


def clear_attempts(user):
    _attempts.pop(user, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Never log secrets — keep access log minimal
        sys.stderr.write("%s - - [%s] %s\n" %
                         (self.client_address[0],
                          self.log_date_time_string(),
                          format % args))

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-User, X-Auth, Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, code, data, content_type="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        user = self.headers.get("X-User")
        auth = self.headers.get("X-Auth")
        return user, auth

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    # ---------- routing ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        if path == "/api/ping":
            self._send_json(200, {"ok": True})
            return

        if path == "/api/kdf":
            user = qs.get("user", [""])[0]
            if not user:
                self._send_json(400, {"error": "user required"})
                return
            vault = load_vault(user)
            if vault:
                self._send_json(200, {
                    "salt": vault.get("salt"),
                    "iter": vault.get("iter", 600000),
                    "rev": vault.get("rev", 0),
                })
            else:
                # Return a fresh salt so client can derive without revealing existence
                self._send_json(200, {
                    "salt": secrets.token_bytes(16).hex(),
                    "iter": 600000,
                    "rev": 0,
                })
            return

        if path == "/api/vault":
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            allowed, wait = check_attempts(user)
            if not allowed:
                self._send_json(429, {"error": "too many attempts", "wait": wait})
                return
            vault = load_vault(user)
            if not vault:
                self._send_json(404, {"error": "no vault"})
                return
            # Constant-time-ish check of auth credential
            stored = vault.get("auth_hash", "")
            if not _secure_compare(auth, stored):
                record_failure(user)
                self._send_json(401, {"error": "invalid credentials"})
                return
            clear_attempts(user)
            self._send_json(200, {
                "v": vault.get("v", 3),
                "salt": vault.get("salt"),
                "iter": vault.get("iter", 600000),
                "pw": vault.get("pw"),
                "rs": vault.get("rs"),
                "iv": vault.get("iv"),
                "ct": vault.get("ct"),
                "rev": vault.get("rev", 0),
            })
            return

        if path.startswith("/api/doc/"):
            doc_id = path[len("/api/doc/"):]
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            p = doc_path(user, doc_id)
            if not os.path.exists(p):
                self._send_json(404, {"error": "not found"})
                return
            with open(p, "rb") as f:
                data = f.read()
            self._send_file(200, data)
            return

        if path.startswith("/api/blob/"):
            blob_id = path[len("/api/blob/"):]
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            p = blob_path(user, blob_id)
            if not os.path.exists(p):
                self._send_json(404, {"error": "not found"})
                return
            with open(p, "rb") as f:
                data = f.read()
            self._send_file(200, data)
            return

        # Static file serving
        if path == "/" or path == "":
            path = "/index.html"
        fs_path = os.path.join(WEB_DIR, path.lstrip("/"))
        if os.path.isfile(fs_path):
            with open(fs_path, "rb") as f:
                data = f.read()
            ct = "text/html" if path.endswith(".html") else "application/octet-stream"
            self._send_file(200, data, ct)
            return

        self._send_json(404, {"error": "not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/vault":
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            allowed, wait = check_attempts(user)
            if not allowed:
                self._send_json(429, {"error": "too many attempts", "wait": wait})
                return
            vault = load_vault(user)
            if not vault:
                self._send_json(404, {"error": "no vault"})
                return
            if not _secure_compare(auth, vault.get("auth_hash", "")):
                record_failure(user)
                self._send_json(401, {"error": "invalid credentials"})
                return

            # Revision check
            try:
                body = json.loads(self._read_body())
            except Exception:
                self._send_json(400, {"error": "invalid json"})
                return
            base_rev = body.get("baseRev")
            if base_rev is not None and base_rev != vault.get("rev", 0):
                self._send_json(409, {"error": "revision mismatch", "serverRev": vault.get("rev", 0)})
                return

            vault_data = body.get("vault")
            if not vault_data:
                self._send_json(400, {"error": "vault required"})
                return

            vault.update({
                "v": vault_data.get("v", vault.get("v", 3)),
                "salt": vault_data.get("salt", vault.get("salt")),
                "iter": vault_data.get("iter", vault.get("iter", 600000)),
                "pw": vault_data.get("pw", vault.get("pw")),
                "rs": vault_data.get("rs", vault.get("rs")),
                "iv": vault_data.get("iv"),
                "ct": vault_data.get("ct"),
                "auth_hash": vault_data.get("auth_hash", vault.get("auth_hash")),
                "rev": vault.get("rev", 0) + 1,
            })
            save_vault(user, vault)
            clear_attempts(user)
            self._send_json(200, {"ok": True, "rev": vault["rev"]})
            return

        if path.startswith("/api/doc/"):
            doc_id = path[len("/api/doc/"):]
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            p = doc_path(user, doc_id)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            data = self._read_body()
            with open(p, "wb") as f:
                f.write(data)
            self._send_json(200, {"ok": True})
            return

        if path.startswith("/api/blob/"):
            blob_id = path[len("/api/blob/"):]
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            p = blob_path(user, blob_id)
            data = self._read_body()
            with open(p, "wb") as f:
                f.write(data)
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/account":
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            vault = load_vault(user)
            if not vault or not _secure_compare(auth, vault.get("auth_hash", "")):
                self._send_json(401, {"error": "invalid credentials"})
                return
            import shutil
            d = user_dir(user)
            if os.path.exists(d):
                shutil.rmtree(d)
            self._send_json(200, {"ok": True})
            return

        if path.startswith("/api/doc/"):
            doc_id = path[len("/api/doc/"):]
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            p = doc_path(user, doc_id)
            if os.path.exists(p):
                os.remove(p)
            self._send_json(200, {"ok": True})
            return

        if path.startswith("/api/blob/"):
            blob_id = path[len("/api/blob/"):]
            user, auth = self._auth()
            if not user or not auth:
                self._send_json(401, {"error": "auth required"})
                return
            p = blob_path(user, blob_id)
            if os.path.exists(p):
                os.remove(p)
            self._send_json(200, {"ok": True})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/register":
            try:
                body = json.loads(self._read_body())
            except Exception:
                self._send_json(400, {"error": "invalid json"})
                return
            user = body.get("user", "").strip()
            invite = body.get("invite_code", "").strip()
            pw_hash = body.get("auth_hash", "").strip()
            salt = body.get("salt", "")
            iter_count = body.get("iter", 600000)

            if not user or not pw_hash:
                self._send_json(400, {"error": "user and auth_hash required"})
                return

            if INVITE_CODE and invite != INVITE_CODE:
                self._send_json(403, {"error": "invalid invite code"})
                return

            if USER_CAP > 0:
                existing = [d for d in os.listdir(DATA_DIR)
                            if os.path.isdir(os.path.join(DATA_DIR, d))]
                if len(existing) >= USER_CAP:
                    self._send_json(403, {"error": "user cap reached"})
                    return

            if load_vault(user):
                self._send_json(409, {"error": "user already exists"})
                return

            vault = {
                "v": 3,
                "salt": salt,
                "iter": iter_count,
                "pw": {"iv": "", "wk": ""},
                "rs": None,
                "iv": "",
                "ct": "",
                "auth_hash": pw_hash,
                "rev": 0,
            }
            save_vault(user, vault)
            self._send_json(200, {"ok": True, "user": user})
            return

        self._send_json(404, {"error": "not found"})


def _secure_compare(a, b):
    if not a or not b:
        return False
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def main():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Build Ledger server on http://0.0.0.0:{PORT}")
    print(f"DATA_DIR={DATA_DIR}  DOCS_DIR={DOCS_DIR}  WEB_DIR={WEB_DIR}")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
