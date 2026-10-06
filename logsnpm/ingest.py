"""Ingestão incremental dos access logs do NPM (arquivo ativo + rotacionados .gz).

Cada arquivo é identificado por um fingerprint (sha1 de base + primeira linha), não pelo
nome: `proxy-host-30_access.log` vira `.1.gz`, depois `.2.gz`, e o fingerprint acompanha.
O offset (bytes já lidos, descomprimidos) é gravado na MESMA transação dos agregados,
então uma queda no meio não duplica nem perde contagem.
"""
import calendar
import glob
import gzip
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import time

from . import classify, config, store

log = logging.getLogger("ingest")

PROXY_RE = re.compile(
    r'^\[(?P<t>[^\]]+)\] (?P<cache>\S*) (?P<us>.*?) (?P<st>\d{3}) - (?P<m>\S*) (?P<sch>\S*) (?P<host>\S*) '
    r'"(?P<uri>[^"]*)" \[Client (?P<ip>[^\]]*)\] \[Length (?P<len>[^\]]*)\] \[Gzip [^\]]*\] '
    r'\[Sent-to [^\]]*\] "(?P<ua>[^"]*)" "(?P<ref>[^"]*)"\s*$')
STANDARD_RE = re.compile(
    r'^\[(?P<t>[^\]]+)\] (?P<st>\d{3}) - (?P<m>\S*) (?P<sch>\S*) (?P<host>\S*) '
    r'"(?P<uri>[^"]*)" \[Client (?P<ip>[^\]]*)\] \[Length (?P<len>[^\]]*)\] \[Gzip [^\]]*\] '
    r'"(?P<ua>[^"]*)" "(?P<ref>[^"]*)"\s*$')
REF_HOST = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/:?#@]+@)?([^/:?#]+)")
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
SUFFIX = re.compile(r"(\.\d+)?(\.gz)?$")
SITE_FROM_NAME = re.compile(r"^(proxy-host|dead-host|redirection-host)-(\d+)_access\.log$")
MAX_MALFORMED_SAMPLES = 200


def parse_time(t):
    """'05/Oct/2026:10:27:42 -0900' -> epoch UTC (int). Respeita o offset da própria linha."""
    try:
        mon = MONTHS[t[3:6]]
        sec = calendar.timegm((int(t[7:11]), mon, int(t[0:2]), int(t[12:14]), int(t[15:17]), int(t[18:20])))
        sign = -1 if t[21] == "-" else 1
        off = sign * (int(t[22:24]) * 3600 + int(t[24:26]) * 60)
        return sec - off
    except (KeyError, ValueError, IndexError):
        return None


def site_of(base):
    m = SITE_FROM_NAME.match(base)
    if not m:
        return 0  # fallback (requisição a host não configurado)
    n = int(m.group(2))
    return {"proxy-host": 0, "dead-host": 10000, "redirection-host": 20000}[m.group(1)] + n


class Ingester:
    def __init__(self, cfg):
        self.cfg = cfg
        paths = cfg["paths"]
        self.db_path = config.db_path(cfg)
        self.log_dir = paths["log_dir"]
        self.globs = cfg["ingest"]["log_globs"]
        self.batch_lines = int(cfg["ingest"]["batch_lines"])
        self.exclude_hosts = {h.lower() for h in cfg["ingest"]["exclude_hosts"]}
        self.v4, self.v6 = cfg["privacy"]["ipv4_prefix"], cfg["privacy"]["ipv6_prefix"]
        self.clf = classify.Classifier(cfg)
        self.conn = store.connect(self.db_path)
        self.secret = self._secret()
        self.offsets_seen = set(filter(None, (self.meta("log_offsets") or "").split(",")))
        self.geo_city = self.geo_asn = None
        if paths["geoip_city"] or paths["geoip_asn"]:
            try:
                import maxminddb
                if paths["geoip_city"] and os.path.exists(paths["geoip_city"]):
                    self.geo_city = maxminddb.open_database(paths["geoip_city"])
                if paths["geoip_asn"] and os.path.exists(paths["geoip_asn"]):
                    self.geo_asn = maxminddb.open_database(paths["geoip_asn"])
            except ImportError:
                log.warning("pacote maxminddb ausente: sem país/ASN")
        self._caches()
        self.reload_rules()
        fp = config.rules_fingerprint(cfg)
        if self.meta("rules_fp") is None:
            self.set_meta("rules_fp", fp)
        elif self.meta("rules_fp") != fp:
            log.warning("regras de classificação mudaram desde a ingestão; rode `logsnpm reindex` para reprocessar")

    def meta(self, k):
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def set_meta(self, k, v):
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (k, str(v)))
        self.conn.commit()

    def reset(self):
        """Apaga agregados e estado de leitura (reindex). Mantém a chave HMAC."""
        for t in ("hits", "path_hits", "ip_hits", "ua_hits", "ref_hits", "files", "malformed",
                  "paths", "uas", "refs", "ips", "hosts", "bots", "meta"):
            self.conn.execute(f"DELETE FROM {t}")
        self.conn.commit()
        self.offsets_seen = set()
        self._caches()
        self.set_meta("rules_fp", config.rules_fingerprint(self.cfg))

    # ---------------------------------------------------------------- setup
    def _secret(self):
        p = os.path.join(os.path.dirname(self.db_path), "ip-hmac.key")
        if not os.path.exists(p):
            with open(p, "w") as f:
                f.write(secrets.token_hex(32))
            os.chmod(p, 0o600)
        with open(p) as f:
            return bytes.fromhex(f.read().strip())

    def _caches(self):
        c = self.conn
        self.hosts = {n: i for i, n in c.execute("SELECT id, name FROM hosts")}
        self.bots = {n: i for i, n in c.execute("SELECT id, name FROM bots")}
        for name, grp in self.clf.bots():
            if name not in self.bots:
                cur = c.execute("INSERT INTO bots(name, grp) VALUES(?, ?)", (name, grp))
                self.bots[name] = cur.lastrowid
        c.commit()
        self.not_bot_id = self.bots[classify.NOT_BOT]
        self.refs = {n: i for i, n in c.execute("SELECT id, name FROM refs")}
        self.paths, self.uas, self.ips, self.ua_bot = {}, {}, {}, {}

    def reload_rules(self):
        sites = store.read_npm_sites(self.cfg["paths"]["npm_db"])
        self.ua_rules = {s["id"]: s["ua_rules"] for s in sites if s["ua_rules"]}
        for sid, override in self.cfg["sites"].items():
            if "ua_rules" in override:
                self.ua_rules[sid] = [{
                    "pattern": rule["pattern"], "status": rule["status"],
                    "rx": re.compile(rule["pattern"], re.I),
                } for rule in override["ua_rules"]]
        self.blocked_nets = store.read_blocked_ips(self.cfg["paths"]["nginx_custom_dir"],
                                                   self.cfg["ingest"]["blocked_ip_geo_var"])

    # ---------------------------------------------------------------- dicionários
    def host_id(self, name, site):
        i = self.hosts.get(name)
        if i is None:
            cur = self.conn.execute("INSERT OR IGNORE INTO hosts(name, site) VALUES(?, ?)", (name, site))
            i = cur.lastrowid if cur.rowcount else self.conn.execute("SELECT id FROM hosts WHERE name=?", (name,)).fetchone()[0]
            self.hosts[name] = i
        return i

    def _dict_id(self, cache, table, cols, key, limit=400000):
        i = cache.get(key)
        if i is not None:
            return i
        vals = key if isinstance(key, tuple) else (key,)
        where = " AND ".join(f"{c}=?" for c in cols)
        row = self.conn.execute(f"SELECT id FROM {table} WHERE {where}", vals).fetchone()
        if row:
            i = row[0]
        else:
            i = self.conn.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals).lastrowid
        if len(cache) > limit:
            cache.clear()
        cache[key] = i
        return i

    def ip_id(self, ip):
        i = self.ips.get(ip)
        if i is not None:
            return i
        h = hmac.new(self.secret, ip.encode(), hashlib.sha256).hexdigest()[:20]
        row = self.conn.execute("SELECT id FROM ips WHERE h=?", (h,)).fetchone()
        if row:
            i = row[0]
        else:
            prefix, cc, asn, org = "?", None, None, None
            try:
                a = ipaddress.ip_address(ip)
                if not a.is_global:
                    prefix = "private"
                else:
                    prefix = str(ipaddress.ip_network(f"{ip}/{self.v4 if a.version == 4 else self.v6}", strict=False))
                    if self.geo_city:
                        rec = self.geo_city.get(ip) or {}
                        cc = (rec.get("country") or rec.get("registered_country") or {}).get("iso_code")
                    if self.geo_asn:
                        rec = self.geo_asn.get(ip) or {}
                        asn = rec.get("autonomous_system_number")
                        org = rec.get("autonomous_system_organization")
            except ValueError:
                prefix = "invalid"
            i = self.conn.execute("INSERT INTO ips(h, prefix, cc, asn, org) VALUES(?,?,?,?,?)",
                                  (h, prefix, cc, asn, org)).lastrowid
        if len(self.ips) > 200000:
            self.ips.clear()
        self.ips[ip] = i
        return i

    def classify_bot(self, ua):
        b = self.ua_bot.get(ua)
        if b is None:
            name, _ = self.clf.ua(ua)
            b = self.bots[name]
            if len(self.ua_bot) > 100000:
                self.ua_bot.clear()
            self.ua_bot[ua] = b
        return b

    def is_blocked_ip(self, ip):
        if not self.blocked_nets:
            return False
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in self.blocked_nets)

    # ---------------------------------------------------------------- arquivos
    def candidates(self):
        files = set()
        for g in self.globs:
            files.update(glob.glob(os.path.join(self.log_dir, g)))
        out = []
        for p in files:
            name = os.path.basename(p)
            base = SUFFIX.sub("", name)
            if not base.endswith("_access.log"):
                continue
            out.append((p, base, name.endswith(".gz"), name == base))
        # rotacionados mais antigos primeiro (maior sufixo), ativo por último
        def order(x):
            m = re.search(r"\.(\d+)(\.gz)?$", x[0])
            return (x[1], -(int(m.group(1)) if m else 0))
        return sorted(out, key=order)

    @staticmethod
    def first_line(path, is_gz):
        opener = gzip.open if is_gz else open
        try:
            with opener(path, "rb") as f:
                line = f.readline(4096)
        except (OSError, EOFError):
            return None
        if not line.endswith(b"\n") and not is_gz:
            return None  # primeira linha ainda incompleta
        return line or None

    def run_once(self):
        t0 = time.time()
        self.reload_rules()
        total = 0
        for path, base, is_gz, active in self.candidates():
            try:
                total += self.process(path, base, is_gz, active)
            except Exception:  # um arquivo ruim não derruba o ciclo
                # descarta o lote parcial: agregados e offset só valem juntos
                self.conn.rollback()
                log.exception("falha em %s", path)
                self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('last_error', ?)",
                                  (f"{int(time.time())} {os.path.basename(path)}",))
        dt = time.time() - t0
        self.conn.executemany("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", [
            ("last_cycle_end", str(int(time.time()))), ("last_cycle_secs", f"{dt:.2f}"),
            ("last_cycle_lines", str(total)), ("log_offsets", ",".join(sorted(self.offsets_seen)))])
        self.conn.commit()
        return total, dt

    def process(self, path, base, is_gz, active):
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return 0
        if st.st_size == 0:
            return 0
        c = self.conn
        if is_gz:
            row = c.execute("SELECT fp FROM files WHERE done=1 AND inode=? AND csize=? AND mtime=?",
                            (st.st_ino, st.st_size, st.st_mtime)).fetchone()
            if row:
                c.execute("UPDATE files SET path=? WHERE fp=?", (path, row[0]))
                return 0
        first = self.first_line(path, is_gz)
        if not first:
            return 0
        fp = hashlib.sha1(base.encode() + b"\0" + first).hexdigest()
        rec = c.execute("SELECT offset, done FROM files WHERE fp=?", (fp,)).fetchone()
        if rec is None:
            c.execute("INSERT INTO files(fp, base, path, inode, csize, mtime, updated) VALUES(?,?,?,?,?,?,?)",
                      (fp, base, path, st.st_ino, st.st_size, st.st_mtime, int(time.time())))
            offset, done = 0, 0
        else:
            offset, done = rec
        if done:
            c.execute("UPDATE files SET path=?, inode=?, csize=?, mtime=? WHERE fp=?",
                      (path, st.st_ino, st.st_size, st.st_mtime, fp))
            c.commit()
            return 0
        if not is_gz:
            if st.st_size == offset:
                c.execute("UPDATE files SET path=?, inode=? WHERE fp=?", (path, st.st_ino, fp))
                return 0
            if st.st_size < offset:
                # truncado sem mudar a 1a linha: não dá para saber o que é novo; recomeça do fim.
                log.warning("%s menor que o offset (%d < %d); reposicionando", path, st.st_size, offset)
                c.execute("UPDATE files SET offset=? WHERE fp=?", (st.st_size, fp))
                c.commit()
                return 0
        return self._read(path, base, is_gz, fp, offset, st)

    def _read(self, path, base, is_gz, fp, offset, st):
        site = site_of(base)
        opener = gzip.open if is_gz else open
        n_total = 0
        with opener(path, "rb") as f:
            if offset:
                if is_gz:
                    remaining = offset
                    while remaining:
                        chunk = f.read(min(remaining, 1 << 20))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                else:
                    f.seek(offset)
            pos = offset
            while True:
                batch, nbytes = [], 0
                for raw in f:
                    if not raw.endswith(b"\n") and not is_gz:
                        break  # linha parcial no arquivo ativo: lê no próximo ciclo
                    batch.append(raw)
                    nbytes += len(raw)
                    if len(batch) >= self.batch_lines:
                        break
                if not batch:
                    break
                pos += nbytes
                n_total += len(batch)
                self._ingest_batch(batch, site, base, fp, pos)
                if len(batch) < self.batch_lines:
                    break
        if is_gz:
            self.conn.execute("UPDATE files SET done=1, path=?, inode=?, csize=?, mtime=? WHERE fp=?",
                              (path, st.st_ino, st.st_size, st.st_mtime, fp))
            self.conn.commit()
        if n_total:
            log.info("%s: +%d linhas", os.path.basename(path), n_total)
        return n_total

    # ---------------------------------------------------------------- agregação
    def _ingest_batch(self, batch, site, base, fp, new_offset):
        hits, phits, iphits, uahits, refhits = {}, {}, {}, {}, {}
        malformed, skipped, repaired, first_ts, last_ts = [], 0, 0, None, None
        site_rules = self.ua_rules.get(site, ())
        offsets = self.offsets_seen
        for raw in batch:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if "\x00" in line:
                # blocos de bytes nulos (desligamento abrupto) colados antes de uma linha válida
                clean = line.replace("\x00", "")
                nul = len(line) - len(clean)
                line = clean
                if not line.strip():
                    malformed.append(("bytes nulos", f"‹{nul} bytes nulos›"))
                    continue
                repaired += 1
            m = PROXY_RE.match(line)
            us = None
            if m:
                us = m.group("us")
            else:
                m = STANDARD_RE.match(line)
            if not m:
                if line.strip():
                    malformed.append(("formato", line[:500]))
                continue
            t = m.group("t")
            ts = parse_time(t)
            if ts is None:
                malformed.append(("data", line[:500]))
                continue
            host = m.group("host").lower()
            if host in self.exclude_hosts:
                skipped += 1
                continue
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts
            if t[21:26] not in offsets:
                offsets.add(t[21:26])
            hour = ts - ts % 3600
            status = int(m.group("st"))
            method = m.group("m").upper()
            mid = store.METHOD_IDS.get(method, 9 if method else 0)
            uri = m.group("uri")
            path = uri.split("?", 1)[0][:400] or "/"
            ua = m.group("ua")[:600]
            ip = m.group("ip")
            ln = m.group("len")
            nbytes = int(ln) if ln.isdigit() else 0
            # upstream: última resposta (ex.: "502, 200" -> 200)
            uclass = 0
            if us and us != "-":
                last = us.replace(":", ",").split(",")[-1].strip()
                if last[:1].isdigit():
                    uclass = int(last[0])
            bot = self.classify_bot(ua)
            kind = store.KIND_IDS[self.clf.kind(site, host, method, path)]
            rule = store.RULE_NONE
            if status == 403 or any(r["status"] == status for r in site_rules):
                if any(r["status"] == status and r["rx"].search(ua) for r in site_rules):
                    rule = store.RULE_UA
                elif status == 403 and self.is_blocked_ip(ip):
                    rule = store.RULE_IP
            hid = self.host_id(host, site)
            k = (hour, site, hid, kind, bot, status, mid, uclass, rule)
            v = hits.get(k)
            if v:
                v[0] += 1
                v[1] += nbytes
            else:
                hits[k] = [1, nbytes]
            pid = self._dict_id(self.paths, "paths", ("host", "path"), (hid, path))
            k = (hour, site, pid, kind, status, bot)
            v = phits.get(k)
            if v:
                v[0] += 1
                v[1] += nbytes
            else:
                phits[k] = [1, nbytes]
            k = (hour, site, self.ip_id(ip), bot, kind, status // 100)
            iphits[k] = iphits.get(k, 0) + 1
            k = (hour, site, self._dict_id(self.uas, "uas", ("ua",), ua, 100000), bot)
            uahits[k] = uahits.get(k, 0) + 1
            ref = m.group("ref")
            if ref and ref != "-":
                rm = REF_HOST.match(ref)
                rname = rm.group(2).lower() if rm else "(invalid)"
                rid = self._dict_id(self.refs, "refs", ("name",), rname, 100000)
                k = (hour, site, rid, bot, kind)
                refhits[k] = refhits.get(k, 0) + 1

        c = self.conn
        c.executemany("INSERT INTO hits VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                      "hits=hits+excluded.hits, bytes=bytes+excluded.bytes",
                      [k + tuple(v) for k, v in hits.items()])
        c.executemany("INSERT INTO path_hits VALUES(?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                      "hits=hits+excluded.hits, bytes=bytes+excluded.bytes",
                      [k + tuple(v) for k, v in phits.items()])
        c.executemany("INSERT INTO ip_hits VALUES(?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET hits=hits+excluded.hits",
                      [k + (v,) for k, v in iphits.items()])
        c.executemany("INSERT INTO ua_hits VALUES(?,?,?,?,?) ON CONFLICT DO UPDATE SET hits=hits+excluded.hits",
                      [k + (v,) for k, v in uahits.items()])
        c.executemany("INSERT INTO ref_hits VALUES(?,?,?,?,?,?) ON CONFLICT DO UPDATE SET hits=hits+excluded.hits",
                      [k + (v,) for k, v in refhits.items()])
        if malformed:
            now = int(time.time())
            c.executemany("INSERT INTO malformed(seen, file, reason, line) VALUES(?,?,?,?)",
                          [(now, base, r, ln) for r, ln in malformed[-50:]])
            c.execute("DELETE FROM malformed WHERE id <= (SELECT MAX(id) FROM malformed) - ?", (MAX_MALFORMED_SAMPLES,))
        good = len(batch) - len(malformed) - skipped
        c.execute("UPDATE files SET offset=?, lines=lines+?, malformed=malformed+?, skipped=skipped+?, repaired=repaired+?,"
                  " first_ts=COALESCE(MIN(first_ts, ?), ?, first_ts), last_ts=COALESCE(MAX(last_ts, ?), ?, last_ts),"
                  " updated=? WHERE fp=?",
                  (new_offset, good, len(malformed), skipped, repaired, first_ts, first_ts, last_ts, last_ts,
                   int(time.time()), fp))
        c.commit()

    def close(self):
        for reader in (self.geo_city, self.geo_asn):
            if reader is not None:
                reader.close()
        self.conn.close()

    def prune(self):
        days = int(self.cfg["ingest"]["retention_days"])
        cut = int(time.time()) - days * 86400
        for t in ("path_hits", "ip_hits", "ua_hits", "ref_hits"):
            self.conn.execute(f"DELETE FROM {t} WHERE hour < ?", (cut,))
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('last_prune', ?)", (str(int(time.time())),))
        self.conn.commit()
