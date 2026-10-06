"""API JSON somente leitura sobre os agregados horários (UTC) + arquivos estáticos da interface."""
import base64
import datetime as dt
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import threading
import time
from collections import OrderedDict, defaultdict
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import classify, config, store

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
log = logging.getLogger("api")


@lru_cache(maxsize=64)
def valid_tz(tz):
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


class BadRequest(Exception):
    pass


class Api:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db_path = config.db_path(cfg)
        self.ttl = int(cfg["server"]["cache_ttl"])
        self.clf = classify.Classifier(cfg)
        self.local = threading.local()
        self.cache = OrderedDict()
        self.cache_lock = threading.Lock()
        self.event_list = []
        for e in cfg["events"]:
            ts = e["ts"]
            ts = int((ts if isinstance(ts, dt.datetime) else dt.datetime.fromisoformat(str(ts))).timestamp())
            self.event_list.append({**e, "ts": ts})
        ui = cfg["ui"]
        tzs = [t if isinstance(t, dict) else {"id": t} for t in ui["timezones"]] or [{"id": ui["default_tz"]}, {"id": "UTC"}]
        if ui["default_tz"] not in {t["id"] for t in tzs}:
            tzs.insert(0, {"id": ui["default_tz"]})
        seen = set()
        self.tzs = [t for t in tzs if valid_tz(t["id"]) and not (t["id"] in seen or seen.add(t["id"]))]

    def ui_config(self, p=None):
        ui = self.cfg["ui"]
        return {k: ui[k] for k in ("title", "subtitle", "language", "logo_url", "accent", "default_period", "default_tz",
                                   "pages", "refresh_seconds", "show_caveats", "caveat_text", "links", "colors")} | {
            "tzs": self.tzs, "privacy": self.cfg["privacy"], "interval": self.cfg["ingest"]["interval"]}

    def conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = store.connect(self.db_path, readonly=True)
            self.local.conn = c
        return c

    def q(self, sql, args=()):
        return self.conn().execute(sql, args).fetchall()

    # ------------------------------------------------------------------ cache
    def cached(self, key, fn, ttl=30):
        now = time.time()
        with self.cache_lock:
            hit = self.cache.get(key)
            if hit and now - hit[0] < ttl:
                self.cache.move_to_end(key)
                return hit[1]
        val = fn()
        with self.cache_lock:
            self.cache[key] = (now, val)
            while len(self.cache) > 300:
                self.cache.popitem(last=False)
        return val

    # ------------------------------------------------------------------ dimensões
    def bot_rows(self):
        return self.q("SELECT id, name, grp FROM bots")

    def bot_ids(self):
        rows = self.bot_rows()
        by_name = {n: i for i, n, _ in rows}
        return rows, by_name[classify.NOT_BOT], by_name[classify.EMPTY_UA]

    # ------------------------------------------------------------------ filtros
    def filters(self, p):
        now = int(time.time())
        end_default = now - now % 3600 + 3600
        try:
            t_to = int(p.get("to") or end_default)
            t_from = int(p.get("from") or (t_to - 7 * 86400))
        except ValueError:
            raise BadRequest("from/to devem ser epoch em segundos")
        if t_from >= t_to:
            raise BadRequest("período inválido")
        h_from = t_from - t_from % 3600
        h_to = t_to - t_to % 3600 + (3600 if t_to % 3600 else 0)
        tz = p.get("tz") or self.cfg["ui"]["default_tz"]
        if not valid_tz(tz):
            raise BadRequest("fuso não suportado")
        f = {"from": h_from, "to": h_to, "tz": tz, "site": None, "host": None,
             "bot": p.get("bot") or "", "status": p.get("status") or "", "kind": p.get("kind") or ""}
        if p.get("site") not in (None, "", "all"):
            try:
                f["site"] = int(p["site"])
            except ValueError:
                raise BadRequest("site inválido")
        if p.get("host"):
            f["host"] = p["host"].lower()
        if f["kind"] and f["kind"] not in store.KIND_IDS:
            raise BadRequest("tipo inválido")
        return f

    def where(self, f, table, alias="", skip=()):
        """Monta WHERE conforme as dimensões que cada tabela possui.

        Retorna (sql, args, ignorados) — `ignorados` lista filtros sem efeito naquela tabela."""
        a = f"{alias}." if alias else ""
        conds, args, ignored = [f"{a}hour >= ?", f"{a}hour < ?"], [f["from"], f["to"]], []
        if f["site"] is not None and "site" not in skip:
            conds.append(f"{a}site = ?")
            args.append(f["site"])
        if f["host"] and "host" not in skip:
            if table == "hits":
                conds.append(f"{a}host = (SELECT id FROM hosts WHERE name = ?)")
                args.append(f["host"])
            elif table == "path_hits":
                conds.append(f"{a}path IN (SELECT p.id FROM paths p JOIN hosts h ON h.id = p.host WHERE h.name = ?)")
                args.append(f["host"])
            else:
                ignored.append("host")
        if f["kind"] and "kind" not in skip:
            if table in ("hits", "path_hits", "ip_hits", "ref_hits"):
                conds.append(f"{a}kind = ?")
                args.append(store.KIND_IDS[f["kind"]])
            else:
                ignored.append("kind")
        b = f["bot"]
        if b and "bot" not in skip:
            _, not_bot, empty = self.bot_ids()
            if b == "bots":
                conds.append(f"{a}bot NOT IN (?, ?)")
                args += [not_bot, empty]
            elif b == "nonbot":
                conds.append(f"{a}bot = ?")
                args.append(not_bot)
            elif b == "noua":
                conds.append(f"{a}bot = ?")
                args.append(empty)
            elif b.startswith("g:"):
                conds.append(f"{a}bot IN (SELECT id FROM bots WHERE grp = ?)")
                args.append(b[2:])
            elif b.startswith("b:") and b[2:].isdigit():
                conds.append(f"{a}bot = ?")
                args.append(int(b[2:]))
            else:
                raise BadRequest("filtro de bot inválido")
        s = f["status"]
        if s and "status" not in skip:
            if table in ("hits", "path_hits"):
                if s in ("1xx", "2xx", "3xx", "4xx", "5xx"):
                    lo = int(s[0]) * 100
                    conds.append(f"{a}status BETWEEN ? AND ?")
                    args += [lo, lo + 99]
                elif s in ("403r", "403o"):
                    if table == "hits":
                        conds.append(f"{a}status = 403 AND {a}rule {'!=' if s == '403r' else '='} 0")
                    else:
                        conds.append(f"{a}status = 403")
                        ignored.append("status-regra")
                elif s.isdigit():
                    conds.append(f"{a}status = ?")
                    args.append(int(s))
                else:
                    raise BadRequest("filtro de status inválido")
            elif table == "ip_hits":
                cls = int(s[0]) if s[:1].isdigit() else None
                if cls:
                    conds.append(f"{a}sclass = ?")
                    args.append(cls)
                    if not s.endswith("xx"):
                        ignored.append("status-exato")
            else:
                ignored.append("status")
        return " AND ".join(conds), args, ignored

    # ------------------------------------------------------------------ buckets
    def bucketer(self, f, mode):
        span = f["to"] - f["from"]
        if mode not in ("hour", "day"):
            mode = "hour" if span <= 3 * 86400 else "day"
        tz = ZoneInfo(f["tz"])
        mapping, order = {}, []
        h = f["from"]
        while h < f["to"]:
            if mode == "hour":
                b = h
            else:
                d = dt.datetime.fromtimestamp(h, tz).date()
                b = int(dt.datetime(d.year, d.month, d.day, tzinfo=tz).timestamp())
            mapping[h] = b
            if not order or order[-1] != b:
                order.append(b)
            h += 3600
        index = {b: i for i, b in enumerate(order)}
        return mode, mapping, order, index

    def series(self, f, rows, mode):
        """rows: (hour, key, value). Retorna buckets e dict key -> lista."""
        mode, mapping, order, index = self.bucketer(f, mode)
        out = defaultdict(lambda: [0] * len(order))
        for hour, key, val in rows:
            b = mapping.get(hour)
            if b is None:
                continue
            out[key][index[b]] += val
        return mode, [b * 1000 for b in order], out

    # ------------------------------------------------------------------ endpoints
    def meta(self, p):
        sites = store.read_npm_sites(self.cfg["paths"]["npm_db"])
        overrides = self.cfg["sites"]
        now = int(time.time())
        rng = self.q("SELECT MIN(hour), MAX(hour) FROM hits")[0]
        per_site = {r[0]: r[1:] for r in self.q(
            "SELECT site, SUM(hits), SUM(CASE WHEN hour >= ? THEN hits ELSE 0 END), MAX(hour) FROM hits GROUP BY site",
            (now - 7 * 86400,))}
        hosts = defaultdict(list)
        for name, site in self.q("SELECT name, site FROM hosts ORDER BY name"):
            hosts[site].append(name)
        known = set()
        out_sites = []
        for s in sites:
            known.add(s["id"])
            tot = per_site.get(s["id"], (0, 0, None))
            o = overrides.get(s["id"], {})
            out_sites.append({
                "id": s["id"], "type": s["type"], "domains": s["domains"], "forward": s["forward"],
                "name": o.get("name"), "hidden": bool(o.get("hidden")),
                "enabled": s["enabled"], "deleted": s["deleted"], "ssl": s["ssl"],
                "ua_rules": [{"pattern": r["pattern"], "status": r["status"]} for r in s["ua_rules"]],
                "hits_total": tot[0] or 0, "hits_7d": tot[1] or 0, "last_hour": tot[2],
                "hosts": hosts.get(s["id"], []),
            })
        for sid, tot in per_site.items():
            if sid not in known:
                o = overrides.get(sid, {})
                out_sites.append({
                    "id": sid, "type": "fallback" if sid == 0 else "removed",
                    "domains": hosts.get(sid, [])[:1] if sid else [], "name": o.get("name"), "hidden": bool(o.get("hidden")),
                    "forward": None, "enabled": sid == 0, "deleted": sid != 0, "ssl": False, "ua_rules": [],
                    "hits_total": tot[0] or 0, "hits_7d": tot[1] or 0, "last_hour": tot[2],
                    "hosts": hosts.get(sid, [])[:20],
                })
        seen = dict(self.q("SELECT bot, SUM(hits) FROM hits GROUP BY bot"))
        bots = [{"id": i, "name": n, "group": g, "hits": seen.get(i, 0)} for i, n, g in self.bot_rows()]
        metam = dict(self.q("SELECT k, v FROM meta"))
        last_ts = self.q("SELECT MAX(last_ts) FROM files")[0][0]
        return {
            "now": now, "default_tz": self.cfg["ui"]["default_tz"], "tzs": self.tzs,
            "data_from": rng[0], "data_to": rng[1],
            "sites": out_sites, "bots": bots, "groups": classify.GROUPS,
            "not_bot": classify.NOT_BOT, "empty_ua": classify.EMPTY_UA,
            "events": self.event_list, "last_event_ts": last_ts,
            "last_cycle_end": int(metam.get("last_cycle_end", 0) or 0),
            "log_offsets": [o for o in (metam.get("log_offsets") or "").split(",") if o],
            "reindex_needed": metam.get("rules_fp") not in (None, config.rules_fingerprint(self.cfg)),
            "blocked_ips": len(store.read_blocked_ips(self.cfg["paths"]["nginx_custom_dir"], self.cfg["ingest"]["blocked_ip_geo_var"])),
        }

    def _totals(self, f):
        w, a, _ = self.where(f, "hits")
        _, not_bot, empty = self.bot_ids()
        t = defaultdict(int)
        for kind, bot, status, rule, uclass, hits, nbytes in self.q(
                f"SELECT kind, bot, status, rule, uclass, SUM(hits), SUM(bytes) FROM hits WHERE {w}"
                " GROUP BY kind, bot, status, rule, uclass", a):
            t["total"] += hits
            t["bytes"] += nbytes
            t[store.KIND_NAMES[kind]] += hits
            if bot == not_bot:
                t["nonbot"] += hits
                if kind == 0:
                    t["nonbot_html"] += hits
            elif bot == empty:
                t["noua"] += hits
            else:
                t["bot"] += hits
                if kind == 0:
                    t["bot_html"] += hits
            t[f"s{status // 100}xx"] += hits
            if status == 403:
                t["s403"] += hits
                t["s403_rule" if rule else "s403_other"] += hits
            elif status == 429:
                t["s429"] += hits
            elif status == 404:
                t["s404"] += hits
            elif status == 499:
                t["s499"] += hits
            if status >= 500:
                t["s5xx_noup" if uclass == 0 else "s5xx_up"] += hits
        return dict(t)

    def _ips(self, f):
        w, a, ign = self.where(f, "ip_hits", "i")
        r = self.q(f"SELECT COUNT(DISTINCT i.ip), COUNT(DISTINCT CASE WHEN x.prefix NOT LIKE 'rede local%' THEN i.ip END)"
                   f" FROM ip_hits i JOIN ips x ON x.id = i.ip WHERE {w}", a)[0]
        return {"all": r[0], "public": r[1], "ignored": ign}

    def overview(self, p):
        f = self.filters(p)
        cur = self._totals(f)
        span = f["to"] - f["from"]
        prev = self._totals({**f, "from": f["from"] - span, "to": f["from"]})
        w, a, _ = self.where(f, "hits")
        rows = self.q(f"SELECT hour, kind, SUM(hits) FROM hits WHERE {w} GROUP BY hour, kind", a)
        mode, buckets, s = self.series(f, [(h, store.KIND_NAMES[k], v) for h, k, v in rows], p.get("bucket"))
        _, not_bot, empty = self.bot_ids()
        rows = self.q(f"SELECT hour, CASE WHEN bot = ? THEN 'nonbot' WHEN bot = ? THEN 'noua' ELSE 'bot' END b,"
                      f" SUM(hits) FROM hits WHERE {w} GROUP BY hour, b", [not_bot, empty] + a)
        _, _, sb = self.series(f, rows, mode)
        # sites
        ws, as_, _ = self.where(f, "hits", skip=("site",))
        site_rows = self.q(f"SELECT site, SUM(hits), SUM(CASE WHEN kind = 0 THEN hits END),"
                           f" SUM(CASE WHEN bot NOT IN (?, ?) THEN hits END), SUM(CASE WHEN status >= 500 THEN hits END),"
                           f" SUM(CASE WHEN status = 403 AND rule != 0 THEN hits END)"
                           f" FROM hits WHERE {ws} GROUP BY site ORDER BY 2 DESC", [not_bot, empty] + as_)
        spark_rows = self.q(f"SELECT hour, site, SUM(hits) FROM hits WHERE {ws} GROUP BY hour, site", as_)
        _, _, spk = self.series(f, spark_rows, mode)
        sites = [{"site": r[0], "hits": r[1], "html": r[2] or 0, "bot": r[3] or 0, "s5xx": r[4] or 0,
                  "s403_rule": r[5] or 0, "spark": spk.get(r[0], [])} for r in site_rows]
        bots = self.q(f"SELECT b.name, b.grp, SUM(h.hits), SUM(CASE WHEN h.kind = 0 THEN h.hits END) FROM hits h"
                      f" JOIN bots b ON b.id = h.bot WHERE {self.where(f, 'hits', 'h')[0]} AND h.bot NOT IN (?, ?)"
                      f" GROUP BY h.bot ORDER BY 3 DESC LIMIT 8", self.where(f, "hits", "h")[1] + [not_bot, empty])
        return {
            "filters": f, "totals": cur, "previous": prev, "ips": self._ips(f),
            "bucket": mode, "buckets": buckets,
            "kind_series": {k: s.get(k, [0] * len(buckets)) for k in store.KIND_IDS},
            "botflag_series": {k: sb.get(k, [0] * len(buckets)) for k in ("bot", "nonbot", "noua")},
            "sites": sites,
            "top_bots": [{"name": r[0], "group": r[1], "hits": r[2], "html": r[3] or 0} for r in bots],
            "events": self._events_in(f),
        }

    def _events_in(self, f):
        return [e for e in self.event_list if f["from"] <= e["ts"] < f["to"] and (f["site"] is None or e.get("site") in (None, f["site"]))]

    def timeseries(self, p):
        f = self.filters(p)
        group = p.get("group", "kind")
        top = min(int(p.get("top") or 8), 20)
        _, not_bot, empty = self.bot_ids()
        w, a, ign = self.where(f, "hits", "h")
        names = {}
        if group == "kind":
            expr, names = "h.kind", {i: n for n, i in store.KIND_IDS.items()}
        elif group == "sclass":
            expr = "h.status / 100"
            names = {i: f"{i}xx" for i in range(1, 6)}
        elif group == "status":
            expr = "h.status"
        elif group == "status403":
            expr = "CASE WHEN h.status = 403 AND h.rule = 1 THEN 'regra UA' WHEN h.status = 403 AND h.rule = 2 THEN 'regra IP' WHEN h.status = 403 THEN 'outros 403' END"
            w += " AND h.status = 403"
        elif group == "s5xx":
            expr = "CAST(h.status AS TEXT) || CASE WHEN h.uclass = 0 THEN ' · sem upstream' ELSE '' END"
            w += " AND h.status >= 500"
        elif group == "site":
            expr = "h.site"
        elif group == "bot":
            expr = "h.bot"
            names = {i: n for i, n, _ in self.bot_rows()}
        elif group == "botgroup":
            expr = f"CASE WHEN h.bot = {not_bot} THEN '{classify.NOT_BOT}' ELSE (SELECT grp FROM bots WHERE id = h.bot) END"
        elif group == "botflag":
            expr = f"CASE WHEN h.bot = {not_bot} THEN 'nonbot' WHEN h.bot = {empty} THEN 'noua' ELSE 'bot' END"
        else:
            raise BadRequest("group inválido")
        rows = self.q(f"SELECT h.hour, {expr} g, SUM(h.hits) FROM hits h WHERE {w} GROUP BY h.hour, g", a)
        mode, buckets, s = self.series(f, rows, p.get("bucket"))
        totals = sorted(((sum(v), k) for k, v in s.items() if k is not None), reverse=True)
        keep = [k for _, k in totals[:top]]
        series = [{"key": k, "name": names.get(k, str(k)), "data": s[k]} for k in keep]
        rest = [k for _, k in totals[top:]]
        if rest:
            series.append({"key": "_rest", "name": "outros", "data": [sum(s[k][i] for k in rest) for i in range(len(buckets))]})
        return {"bucket": mode, "buckets": buckets, "series": series, "ignored": ign, "events": self._events_in(f)}

    def bots(self, p):
        f = self.filters(p)
        _, not_bot, empty = self.bot_ids()
        w, a, _ = self.where(f, "hits", "h")
        rows = self.q(
            f"SELECT h.bot, b.name, b.grp, SUM(h.hits), SUM(CASE WHEN h.kind=0 THEN h.hits END),"
            f" SUM(CASE WHEN h.kind=1 THEN h.hits END), SUM(CASE WHEN h.kind=2 THEN h.hits END),"
            f" SUM(CASE WHEN h.kind=3 THEN h.hits END), SUM(CASE WHEN h.status BETWEEN 200 AND 299 THEN h.hits END),"
            f" SUM(CASE WHEN h.status BETWEEN 300 AND 399 THEN h.hits END),"
            f" SUM(CASE WHEN h.status = 403 AND h.rule != 0 THEN h.hits END),"
            f" SUM(CASE WHEN h.status = 403 AND h.rule = 0 THEN h.hits END),"
            f" SUM(CASE WHEN h.status = 429 THEN h.hits END),"
            f" SUM(CASE WHEN h.status BETWEEN 400 AND 499 AND h.status NOT IN (403, 429) THEN h.hits END),"
            f" SUM(CASE WHEN h.status >= 500 THEN h.hits END), MIN(h.hour), MAX(h.hour), SUM(h.bytes)"
            f" FROM hits h JOIN bots b ON b.id = h.bot WHERE {w} GROUP BY h.bot ORDER BY 4 DESC", a)
        wi, ai, _ = self.where(f, "ip_hits", "i")
        ips = dict(self.q(f"SELECT i.bot, COUNT(DISTINCT i.ip) FROM ip_hits i WHERE {wi} GROUP BY i.bot", ai))
        total = sum(r[3] for r in rows) or 1
        table = []
        for r in rows:
            table.append({
                "id": r[0], "name": r[1], "group": r[2] or "—", "hits": r[3], "html": r[4] or 0, "static": r[5] or 0,
                "api": r[6] or 0, "other": r[7] or 0, "s2xx": r[8] or 0, "s3xx": r[9] or 0, "s403_rule": r[10] or 0,
                "s403_other": r[11] or 0, "s429": r[12] or 0, "s4xx_other": r[13] or 0, "s5xx": r[14] or 0,
                "first": r[15], "last": r[16] + 3600, "bytes": r[17], "share": r[3] / total, "ips": ips.get(r[0], 0),
                "is_bot": r[0] not in (not_bot, empty),
            })
        groups = defaultdict(int)
        for t in table:
            groups[t["group"] if t["is_bot"] else t["name"]] += t["hits"]
        return {"table": table, "groups": [{"group": g, "hits": h} for g, h in sorted(groups.items(), key=lambda x: -x[1])],
                "total": total}

    def status(self, p):
        f = self.filters(p)
        _, not_bot, empty = self.bot_ids()
        w, a, _ = self.where(f, "hits", "h")
        rows = self.q(
            f"SELECT h.status, SUM(h.hits), SUM(CASE WHEN h.kind = 0 THEN h.hits END),"
            f" SUM(CASE WHEN h.bot NOT IN (?, ?) THEN h.hits END), SUM(CASE WHEN h.rule = 1 THEN h.hits END),"
            f" SUM(CASE WHEN h.rule = 2 THEN h.hits END), SUM(CASE WHEN h.uclass = 0 THEN h.hits END), MAX(h.hour)"
            f" FROM hits h WHERE {w} GROUP BY h.status ORDER BY 2 DESC", [not_bot, empty] + a)
        codes = [{"status": r[0], "hits": r[1], "html": r[2] or 0, "bot": r[3] or 0,
                  "rule_ua": r[4] or 0, "rule_ip": r[5] or 0, "no_upstream": r[6] or 0, "last": r[7]} for r in rows]
        # 403 por bot (quem está sendo barrado)
        b403 = self.q(f"SELECT b.name, SUM(h.hits), SUM(CASE WHEN h.rule != 0 THEN h.hits END) FROM hits h"
                      f" JOIN bots b ON b.id = h.bot WHERE {w} AND h.status = 403 GROUP BY h.bot ORDER BY 2 DESC LIMIT 10", a)
        # caminhos com 5xx
        wp, ap, _ = self.where(f, "path_hits", "ph")
        p5 = self.q(f"SELECT x.path, ho.name, ph.status, SUM(ph.hits) FROM path_hits ph JOIN paths x ON x.id = ph.path"
                    f" JOIN hosts ho ON ho.id = x.host WHERE {wp} AND ph.status >= 500"
                    f" GROUP BY ph.path, ph.status ORDER BY 4 DESC LIMIT 15", ap)
        return {"codes": codes,
                "by_bot_403": [{"name": r[0], "hits": r[1], "rule": r[2] or 0} for r in b403],
                "paths_5xx": [{"path": r[0], "host": r[1], "status": r[2], "hits": r[3]} for r in p5]}

    def events(self, p):
        win = max(1, min(int(p.get("window") or 24), 24 * 14))
        now = int(time.time())
        out = []
        bots = {n: i for i, n, _ in self.bot_rows()}
        for e in self.event_list:
            res = {"event": e, "window_h": win}
            ts = e["ts"] - e["ts"] % 3600
            now_h = now - now % 3600
            # a hora do evento é mista (antes+depois) e fica separada; "depois" inclui a hora corrente parcial
            windows = (("before", ts - win * 3600, ts, win),
                       ("during", ts, ts + 3600, min(3600, max(now - ts, 0)) / 3600),
                       ("after", ts + 3600, min(ts + 3600 + win * 3600, now_h + 3600),
                        max(min(ts + 3600 + win * 3600, now) - (ts + 3600), 0) / 3600))
            for label, lo, hi, hours in windows:
                cond, args = "hour >= ? AND hour < ?", [lo, hi]
                if e.get("site") is not None:
                    cond += " AND site = ?"
                    args.append(e["site"])
                bot_id = bots.get(e.get("bot"), -1)
                r = self.q(f"SELECT SUM(hits), SUM(CASE WHEN kind=0 THEN hits END), SUM(CASE WHEN bot = ? THEN hits END),"
                           f" SUM(CASE WHEN status=403 AND rule != 0 THEN hits END),"
                           f" SUM(CASE WHEN status=403 AND rule = 0 THEN hits END),"
                           f" SUM(CASE WHEN status >= 500 THEN hits END), SUM(CASE WHEN status BETWEEN 200 AND 299 THEN hits END),"
                           f" SUM(CASE WHEN bot = ? AND status BETWEEN 200 AND 299 THEN hits END)"
                           f" FROM hits WHERE {cond}", [bot_id, bot_id] + args)[0]
                keys = ("total", "html", "bot", "s403_rule", "s403_other", "s5xx", "s2xx", "bot_2xx")
                vals = {k: (v or 0) for k, v in zip(keys, r)}
                res[label] = {"hours": round(hours, 2), "from": lo, "to": hi, **vals,
                              "per_hour": {k: (v / hours if hours >= 0.05 else None) for k, v in vals.items()}}
            bot_id = bots.get(e.get("bot"))
            if bot_id is not None:
                cond, args = "bot = ?", [bot_id]
                if e.get("site") is not None:
                    cond += " AND site = ?"
                    args.append(e["site"])
                last = self.q(f"SELECT MAX(hour) FROM hits WHERE {cond} AND hour < ? AND status BETWEEN 200 AND 299",
                              args + [ts])[0][0]
                after = self.q(f"SELECT status, rule, SUM(hits) FROM hits WHERE {cond} AND hour >= ? GROUP BY status, rule",
                               args + [ts])
                res["bot_info"] = {"last_2xx_hour_before": last,
                                   "after_by_status": [{"status": s, "rule": r, "hits": h} for s, r, h in after]}
            out.append(res)
        return {"events": out}

    def pages(self, p):
        f = self.filters(p)
        _, not_bot, empty = self.bot_ids()
        limit = max(1, min(int(p.get("limit") or 50), 500))
        offset = max(0, int(p.get("offset") or 0))
        w, a, ign = self.where(f, "path_hits", "ph")
        if p.get("q"):
            w += " AND ph.path IN (SELECT id FROM paths WHERE path LIKE ?)"
            a.append(f"%{p['q']}%")
        order = {"hits": "2", "bytes": "3", "bot": "4", "s4xx": "7", "s5xx": "8"}.get(p.get("sort") or "hits", "2")
        rows = self.q(
            f"SELECT ph.path, SUM(ph.hits), SUM(ph.bytes), SUM(CASE WHEN ph.bot NOT IN (?, ?) THEN ph.hits ELSE 0 END),"
            f" SUM(CASE WHEN ph.status BETWEEN 200 AND 299 THEN ph.hits ELSE 0 END),"
            f" SUM(CASE WHEN ph.status BETWEEN 300 AND 399 THEN ph.hits ELSE 0 END),"
            f" SUM(CASE WHEN ph.status BETWEEN 400 AND 499 THEN ph.hits ELSE 0 END),"
            f" SUM(CASE WHEN ph.status >= 500 THEN ph.hits ELSE 0 END), ph.kind,"
            f" SUM(CASE WHEN ph.bot = ? THEN ph.hits ELSE 0 END)"
            f" FROM path_hits ph WHERE {w} GROUP BY ph.path, ph.kind ORDER BY {order} DESC LIMIT ? OFFSET ?",
            [not_bot, empty, empty] + a + [limit + 1, offset])
        more = len(rows) > limit
        rows = rows[:limit]
        info = {}
        if rows:
            ids = ",".join(str(r[0]) for r in rows)
            info = {r[0]: (r[1], r[2], r[3]) for r in self.q(
                f"SELECT p.id, p.path, h.name, h.site FROM paths p JOIN hosts h ON h.id = p.host WHERE p.id IN ({ids})")}
        sum_row = self.q(f"SELECT SUM(ph.hits) FROM path_hits ph WHERE {w}", a)[0][0] or 0
        out = []
        for r in rows:
            path, host, site = info.get(r[0], ("?", "?", None))
            out.append({"path": path, "host": host, "site": site, "hits": r[1], "bytes": r[2], "bot": r[3],
                        "s2xx": r[4], "s3xx": r[5], "s4xx": r[6], "s5xx": r[7], "kind": store.KIND_NAMES[r[8]],
                        "noua": r[9]})
        return {"rows": out, "more": more, "offset": offset, "limit": limit, "total_hits": sum_row, "ignored": ign}

    def geo(self, p):
        f = self.filters(p)
        _, not_bot, empty = self.bot_ids()
        w, a, ign = self.where(f, "ip_hits", "i")
        base = f"FROM ip_hits i JOIN ips x ON x.id = i.ip WHERE {w}"
        botsum = "SUM(CASE WHEN i.bot NOT IN (?, ?) THEN i.hits ELSE 0 END)"
        countries = self.q(f"SELECT x.cc, SUM(i.hits), COUNT(DISTINCT i.ip), {botsum} {base} GROUP BY x.cc ORDER BY 2 DESC",
                           [not_bot, empty] + a)
        asns = self.q(f"SELECT x.asn, MAX(x.org), SUM(i.hits), COUNT(DISTINCT i.ip), {botsum} {base}"
                      f" GROUP BY x.asn ORDER BY 3 DESC LIMIT 25", [not_bot, empty] + a)
        nets = self.q(f"SELECT x.prefix, MAX(x.cc), MAX(x.org), SUM(i.hits), COUNT(DISTINCT i.ip), {botsum} {base}"
                      f" GROUP BY x.prefix ORDER BY 4 DESC LIMIT 25", [not_bot, empty] + a)
        dominant = {}
        if nets:
            marks = ",".join("?" * len(nets))
            for prefix, name, hits in self.q(
                    f"SELECT x.prefix, b.name, SUM(i.hits) FROM ip_hits i JOIN ips x ON x.id = i.ip JOIN bots b ON b.id = i.bot"
                    f" WHERE {w} AND x.prefix IN ({marks}) GROUP BY x.prefix, i.bot", a + [r[0] for r in nets]):
                if hits > dominant.get(prefix, ("", 0))[1]:
                    dominant[prefix] = (name, hits)
        return {
            "countries": [{"cc": r[0], "hits": r[1], "ips": r[2], "bot": r[3]} for r in countries],
            "asns": [{"asn": r[0], "org": r[1], "hits": r[2], "ips": r[3], "bot": r[4]} for r in asns],
            "networks": [{"prefix": r[0], "cc": r[1], "org": r[2], "hits": r[3], "ips": r[4], "bot": r[5],
                          "top_ua_class": dominant.get(r[0], ("", 0))[0]} for r in nets],
            "ips": self._ips(f), "ignored": ign,
        }

    def referrers(self, p):
        f = self.filters(p)
        _, not_bot, empty = self.bot_ids()
        w, a, ign = self.where(f, "ref_hits", "r")
        own = set()
        for s in store.read_npm_sites(self.cfg["paths"]["npm_db"]):
            own.update(d.lower() for d in s["domains"])
        rows = self.q(f"SELECT x.name, SUM(r.hits), SUM(CASE WHEN r.bot NOT IN (?, ?) THEN r.hits ELSE 0 END),"
                      f" SUM(CASE WHEN r.kind = 0 THEN r.hits ELSE 0 END)"
                      f" FROM ref_hits r JOIN refs x ON x.id = r.ref WHERE {w} GROUP BY r.ref ORDER BY 2 DESC LIMIT 40",
                      [not_bot, empty] + a)
        return {"rows": [{"ref": r[0], "hits": r[1], "bot": r[2], "html": r[3], "internal": r[0] in own} for r in rows],
                "ignored": ign}

    def uas(self, p):
        f = self.filters(p)
        w, a, ign = self.where(f, "ua_hits", "u")
        rows = self.q(f"SELECT x.ua, b.name, SUM(u.hits) FROM ua_hits u JOIN uas x ON x.id = u.ua JOIN bots b ON b.id = u.bot"
                      f" WHERE {w} GROUP BY u.ua ORDER BY 3 DESC LIMIT ?", a + [min(int(p.get("limit") or 30), 200)])
        return {"rows": [{"ua": r[0], "bot": r[1], "hits": r[2]} for r in rows], "ignored": ign}

    def health(self, p):
        files = self.q("SELECT fp, base, path, offset, csize, done, lines, malformed, skipped, first_ts, last_ts, updated,"
                       " repaired"
                       " FROM files ORDER BY base, COALESCE(first_ts, 0) DESC")
        sizes = {}
        for r in files:
            try:
                sizes[r[2]] = os.path.getsize(r[2])
            except (OSError, TypeError):
                sizes[r[2]] = None
        metam = dict(self.q("SELECT k, v FROM meta"))
        bad = self.q("SELECT seen, file, reason, line FROM malformed ORDER BY id DESC LIMIT 25")
        counts = {t: self.q(f"SELECT COUNT(*) FROM {t}")[0][0] for t in ("hits", "path_hits", "ip_hits", "ua_hits", "ref_hits", "paths", "uas", "ips")}
        db_size = sum(os.path.getsize(self.db_path + s) for s in ("", "-wal") if os.path.exists(self.db_path + s))
        return {
            "files": [{"fp": r[0][:10], "base": r[1], "path": r[2], "name": os.path.basename(r[2] or ""),
                       "offset": r[3], "size": sizes.get(r[2]), "csize": r[4], "done": bool(r[5]), "lines": r[6],
                       "malformed": r[7], "skipped": r[8], "first_ts": r[9], "last_ts": r[10], "updated": r[11],
                       "repaired": r[12], "exists": sizes.get(r[2]) is not None} for r in files],
            "meta": metam, "malformed": [{"seen": r[0], "file": r[1], "reason": r[2], "line": r[3]} for r in bad],
            "counts": counts, "db_size": db_size, "now": int(time.time()),
            "interval": self.cfg["ingest"]["interval"], "log_offsets": [o for o in (metam.get("log_offsets") or "").split(",") if o],
            "config_path": self.cfg.get("_path"), "reindex_needed": metam.get("rules_fp") not in (None, config.rules_fingerprint(self.cfg)),
            "rules": [{"site": s["id"], "domains": s["domains"],
                       "rules": [{"pattern": r["pattern"], "status": r["status"]} for r in s["ua_rules"]]}
                      for s in store.read_npm_sites(self.cfg["paths"]["npm_db"]) if s["ua_rules"]],
            **self.clf.describe(),
        }

    ROUTES = {"config": "ui_config", "meta": "meta", "overview": "overview", "timeseries": "timeseries", "bots": "bots",
              "status": "status", "events": "events", "pages": "pages", "geo": "geo", "referrers": "referrers",
              "uas": "uas", "health": "health"}

    def handle(self, path, query):
        name = path.strip("/").split("/")[-1]
        meth = self.ROUTES.get(name)
        if not meth:
            return 404, {"error": "rota desconhecida"}
        p = {k: v[0] for k, v in parse_qs(query).items()}
        ttl = min(10, self.ttl) if name in ("health", "meta") else self.ttl
        try:
            return 200, self.cached(f"{name}?{query}", lambda: getattr(self, meth)(p), ttl)
        except BadRequest as e:
            return 400, {"error": str(e)}
        except ValueError as e:
            return 400, {"error": f"parâmetro inválido: {e}"}


def make_server(api, cfg):
    srv_cfg = cfg["server"]
    nets = [ipaddress.ip_network(n, strict=False) for n in srv_cfg["allow_networks"]]
    trust_xff = bool(srv_cfg["trust_x_forwarded_for"])
    user, pwd = srv_cfg["auth_user"], srv_cfg["auth_password"]
    expected = ("Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()) if user else None
    web_root = os.path.realpath(WEB_DIR)

    class Handler(BaseHTTPRequestHandler):
        server_version = "logsNPM"
        protocol_version = "HTTP/1.1"

        def client_ip(self):
            ip = self.client_address[0]
            if trust_xff:
                xff = self.headers.get("X-Forwarded-For")
                if xff:
                    ip = xff.split(",")[0].strip()
            return ip

        def allowed(self):
            if not nets:
                return True
            try:
                a = ipaddress.ip_address(self.client_ip())
            except ValueError:
                return False
            if a.version == 6 and a.ipv4_mapped:
                a = a.ipv4_mapped
            return any(a in n for n in nets)

        def send(self, code, data, ctype, extra=()):
            if len(data) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or "") and not ctype.startswith(("font/", "image/")):
                import gzip
                data = gzip.compress(data, 5)
                extra = (*extra, ("Content-Encoding", "gzip"), ("Vary", "Accept-Encoding"))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.allowed():
                return self.send(403, b"forbidden", "text/plain")
            if expected and not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                return self.send(401, b"auth required", "text/plain", (("WWW-Authenticate", 'Basic realm="logsNPM"'),))
            u = urlparse(self.path)
            if u.path.startswith("/api/"):
                t0 = time.time()
                try:
                    code, body = api.handle(u.path, u.query)
                except Exception as e:  # noqa: BLE001
                    log.exception("erro em %s", self.path)
                    code, body = 500, {"error": f"{type(e).__name__}: {e}"}
                data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
                return self.send(code, data, "application/json; charset=utf-8",
                                 (("Cache-Control", "no-store"), ("X-Elapsed-Ms", f"{(time.time() - t0) * 1000:.0f}")))
            rel = unquote(u.path).lstrip("/") or "index.html"
            full = os.path.realpath(os.path.join(web_root, rel))
            if not full.startswith(web_root + os.sep) or not os.path.isfile(full):
                return self.send(404, b"not found", "text/plain")
            st = os.stat(full)
            etag = f'"{int(st.st_mtime)}-{st.st_size}"'
            cache = "public, max-age=2592000" if rel.startswith("vendor/") else "no-cache"
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            with open(full, "rb") as f:
                data = f.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self.send(200, data, ctype, (("ETag", etag), ("Cache-Control", cache)))

        def log_message(self, fmt, *args):
            pass

    srv = ThreadingHTTPServer((srv_cfg["listen"], int(srv_cfg["port"])), Handler)
    srv.daemon_threads = True
    return srv
