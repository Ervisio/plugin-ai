"""Settings of the headless server (``server.json``)."""
import os
import socket
import subprocess
from typing import Any, Dict, List

from . import paths
from .util import read_json, write_json

DEFAULT_PORT = 7420

DEFAULTS = {
    "bind": "127.0.0.1",  # 127.0.0.1 = this machine only (reach it through SSH); 0.0.0.0 = every interface
    "port": DEFAULT_PORT,
    "tls": "none",  # none | self-signed | custom
    "cert": "",
    "key": "",
    "allowed_origins": [],
    "allowed_hosts": [],
}  # type: Dict[str, Any]


def config_file() -> str:
    return os.path.join(paths.server_dir(), "server.json")


def load() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    data = read_json(config_file(), {})
    if isinstance(data, dict):
        for k, v in data.items():
            if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                cfg[k] = v
    return cfg


def save(update: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load()
    for k, v in update.items():
        if k not in DEFAULTS:
            raise ValueError("unknown setting: %s" % k)
        if type(v) is not type(DEFAULTS[k]):
            raise ValueError("setting %s must be %s" % (k, type(DEFAULTS[k]).__name__))
        cfg[k] = v
    if not 1 <= int(cfg["port"]) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if cfg["tls"] not in ("none", "self-signed", "custom"):
        raise ValueError("tls must be none, self-signed or custom")
    if cfg["tls"] == "custom" and not (cfg["cert"] and cfg["key"]):
        raise ValueError("tls=custom needs cert and key paths")
    write_json(config_file(), cfg, 0o644)
    return cfg


def tls_dir() -> str:
    return paths.sub("tls", create=True)


def self_signed(names: List[str]) -> Dict[str, str]:
    """Make (or reuse) a self-signed certificate for this machine with openssl; returns its paths and fingerprint."""
    cert, key = os.path.join(tls_dir(), "server.crt"), os.path.join(tls_dir(), "server.key")
    if not (os.path.exists(cert) and os.path.exists(key)):
        host = socket.gethostname()
        sans = ["DNS:localhost", "IP:127.0.0.1", "DNS:" + host] + [("IP:" if _is_ip(n) else "DNS:") + n for n in names if n]
        cmd = ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "825",
               "-subj", "/CN=%s" % host, "-addext", "subjectAltName=" + ",".join(dict.fromkeys(sans)), "-keyout", key, "-out", cert]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError("openssl is needed to make a certificate: %s" % e)
        if r.returncode != 0:
            raise RuntimeError("openssl failed: %s" % r.stderr.decode("utf-8", "replace").strip())
        os.chmod(key, 0o600)
    fp = subprocess.run(["openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha256"], stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL).stdout.decode().strip().split("=", 1)[-1]
    return {"cert": cert, "key": key, "fingerprint": fp}


def _is_ip(value: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, value)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, value)
        return True
    except OSError:
        return False
