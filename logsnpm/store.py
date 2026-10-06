"""Esquema SQLite e leitura (somente leitura) do banco e das configs do NPM."""
import json
import os
import re
import sqlite3
import ipaddress

KIND_IDS = {"html": 0, "static": 1, "api": 2, "other": 3}
KIND_NAMES = {v: k for k, v in KIND_IDS.items()}
METHOD_IDS = {"GET": 1, "HEAD": 2, "POST": 3, "PUT": 4, "PATCH": 5, "DELETE": 6, "OPTIONS": 7, "PROPFIND": 8}
RULE_NONE, RULE_UA, RULE_IP = 0, 1, 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files(
  fp TEXT PRIMARY KEY, base TEXT NOT NULL, path TEXT, inode INTEGER, csize INTEGER, mtime REAL,
  offset INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
  lines INTEGER NOT NULL DEFAULT 0, malformed INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0,
  repaired INTEGER NOT NULL DEFAULT 0, first_ts INTEGER, last_ts INTEGER, updated INTEGER);
CREATE TABLE IF NOT EXISTS malformed(id INTEGER PRIMARY KEY, seen INTEGER, file TEXT, reason TEXT, line TEXT);
CREATE TABLE IF NOT EXISTS hosts(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, site INTEGER);
CREATE TABLE IF NOT EXISTS bots(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, grp TEXT);
CREATE TABLE IF NOT EXISTS paths(id INTEGER PRIMARY KEY, host INTEGER NOT NULL, path TEXT NOT NULL, UNIQUE(host, path));
CREATE TABLE IF NOT EXISTS uas(id INTEGER PRIMARY KEY, ua TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS refs(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS ips(id INTEGER PRIMARY KEY, h TEXT NOT NULL UNIQUE, prefix TEXT, cc TEXT, asn INTEGER, org TEXT);
CREATE TABLE IF NOT EXISTS hits(
  hour INTEGER, site INTEGER, host INTEGER, kind INTEGER, bot INTEGER, status INTEGER,
  method INTEGER, uclass INTEGER, rule INTEGER, hits INTEGER, bytes INTEGER,
  PRIMARY KEY(hour, site, host, kind, bot, status, method, uclass, rule)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS path_hits(
  hour INTEGER, site INTEGER, path INTEGER, kind INTEGER, status INTEGER, bot INTEGER, hits INTEGER, bytes INTEGER,
  PRIMARY KEY(hour, site, path, kind, status, bot)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ip_hits(
  hour INTEGER, site INTEGER, ip INTEGER, bot INTEGER, kind INTEGER, sclass INTEGER, hits INTEGER,
  PRIMARY KEY(hour, site, ip, bot, kind, sclass)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ua_hits(
  hour INTEGER, site INTEGER, ua INTEGER, bot INTEGER, hits INTEGER,
  PRIMARY KEY(hour, site, ua, bot)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ref_hits(
  hour INTEGER, site INTEGER, ref INTEGER, bot INTEGER, kind INTEGER, hits INTEGER,
  PRIMARY KEY(hour, site, ref, bot, kind)) WITHOUT ROWID;
"""



def connect(path, readonly=False):
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA query_only=1")
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-65536")
    return conn


# ------------------------------------------------------------------ NPM (somente leitura)
_UA_RULE = re.compile(
    r"if\s*\(\s*\$http_user_agent\s*(~\*?)\s*(\"[^\"]+\"|'[^']+'|[^)\s]+)\s*\)\s*\{\s*return\s+(\d{3})\s*;\s*\}",
    re.I)


def read_npm_sites(npm_db):
    """Proxy hosts (e dead/redirection) do banco do NPM, aberto read-only. Ausente -> []."""
    out = []
    if not npm_db or not os.path.exists(npm_db):
        return out
    try:
        conn = sqlite3.connect(f"file:{npm_db}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(
            "SELECT id, domain_names, forward_scheme, forward_host, forward_port, enabled, is_deleted,"
            " certificate_id, ssl_forced, advanced_config FROM proxy_host").fetchall()
    except sqlite3.Error:
        return out
    try:
        for r in rows:
            rules = []
            for m in _UA_RULE.finditer(r[9] or ""):
                pat = m.group(2).strip("\"'")
                try:
                    rx = re.compile(pat, re.I if m.group(1) == "~*" else 0)
                except re.error:
                    continue
                rules.append({"pattern": pat, "status": int(m.group(3)), "rx": rx})
            out.append({
                "id": r[0], "type": "proxy", "domains": json.loads(r[1] or "[]"),
                "forward": f"{r[2]}://{r[3]}:{r[4]}", "enabled": bool(r[5]), "deleted": bool(r[6]),
                "ssl": bool(r[7]), "ssl_forced": bool(r[8]), "ua_rules": rules,
            })
        for table, offset, kind in (("dead_host", 10000, "404"), ("redirection_host", 20000, "redirect")):
            try:
                for r in conn.execute(f"SELECT id, domain_names, enabled, is_deleted FROM {table}"):
                    out.append({"id": offset + r[0], "type": kind, "domains": json.loads(r[1] or "[]"),
                                "forward": None, "enabled": bool(r[2]), "deleted": bool(r[3]),
                                "ssl": False, "ssl_forced": False, "ua_rules": []})
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return out


def read_blocked_ips(custom_dir, var="blocked_ip"):
    """Redes de um bloco `geo $<var> { ... }` nos .conf do diretório custom do NPM (somente leitura)."""
    nets = []
    if not custom_dir or not var or not os.path.isdir(custom_dir):
        return nets
    block = re.compile(r"geo\s+\$" + re.escape(var) + r"\s*\{([^}]*)\}", re.S)
    for name in sorted(os.listdir(custom_dir)):
        if not name.endswith(".conf"):
            continue
        try:
            with open(os.path.join(custom_dir, name)) as f:
                txt = f.read()
        except OSError:
            continue
        for m in block.finditer(txt):
            for line in m.group(1).splitlines():
                line = line.split("#", 1)[0].strip().rstrip(";")
                parts = line.split()
                if len(parts) == 2 and parts[0] != "default" and parts[1] != "0":
                    try:
                        nets.append(ipaddress.ip_network(parts[0], strict=False))
                    except ValueError:
                        pass
    return nets
