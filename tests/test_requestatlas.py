"""Testes do RequestAtlas (stdlib unittest): `python -m unittest discover -s tests`."""
import base64
import json
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import logging
import unittest
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import Env, proxy_line, standard_line  # noqa: E402
from requestatlas import api, classify, config, ingest, store  # noqa: E402

logging.disable(logging.CRITICAL)


def totals(cfg):
    c = sqlite3.connect(config.db_path(cfg))
    tot = c.execute("SELECT COALESCE(SUM(hits), 0) FROM hits").fetchone()[0]
    by_bot = dict(c.execute("SELECT b.name, SUM(h.hits) FROM hits h JOIN bots b ON b.id = h.bot GROUP BY b.name"))
    rule = c.execute("SELECT COALESCE(SUM(hits), 0) FROM hits WHERE rule = 1").fetchone()[0]
    c.close()
    return tot, by_bot, rule


class ParserTest(unittest.TestCase):
    def test_ua_comes_from_field_after_sent_to_not_referrer(self):
        m = ingest.PROXY_RE.match(proxy_line(1, ua="Mozilla/5.0 Chrome/129", ref="https://x/?q=GPTBot").rstrip("\n"))
        self.assertEqual(m.group("ua"), "Mozilla/5.0 Chrome/129")
        self.assertEqual(m.group("ref"), "https://x/?q=GPTBot")

    def test_multiple_upstreams_and_standard_format(self):
        m = ingest.PROXY_RE.match(proxy_line(1, upstream="502, 200").rstrip("\n"))
        self.assertEqual((m.group("us"), m.group("st")), ("502, 200", "200"))
        self.assertIsNone(ingest.PROXY_RE.match(standard_line().rstrip("\n")))
        self.assertEqual(ingest.STANDARD_RE.match(standard_line().rstrip("\n")).group("ua"), "curl/8.0")

    def test_time_offset_is_honored(self):
        self.assertEqual(ingest.parse_time("05/Oct/2026:10:00:00 -0900"), ingest.parse_time("05/Oct/2026:19:00:00 +0000"))
        self.assertIsNone(ingest.parse_time("garbage"))


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        import copy
        self.cfg = copy.deepcopy(config.DEFAULTS)
        config.validate(self.cfg)

    def test_bot_word_outside_ua_is_not_a_bot(self):
        c = classify.Classifier(self.cfg)
        self.assertEqual(c.ua("Mozilla/5.0 (X11; Linux x86_64) Chrome/129")[0], classify.NOT_BOT)
        self.assertEqual(c.ua("Mozilla/5.0; compatible; GPTBot/1.2; +https://openai.com/gptbot")[0], "GPTBot")
        self.assertEqual(c.ua("Mozilla/5.0 (compatible; Googlebot/2.1)")[0], "Googlebot")
        self.assertEqual(c.ua("Mediapartners-Google")[0], "Mediapartners-Google (AdSense)")
        self.assertEqual(c.ua("-")[0], classify.EMPTY_UA)

    def test_custom_and_disabled_bots(self):
        self.cfg["bots"]["custom"] = [{"name": "MeuMonitor", "pattern": "meu-monitor/"}]
        self.cfg["bots"]["disable"] = ["Go-http-client"]
        c = classify.Classifier(self.cfg)
        self.assertEqual(c.ua("meu-monitor/1.0"), ("MeuMonitor", "custom"))
        self.assertEqual(c.ua("Go-http-client/2.0")[0], classify.NOT_BOT)

    def test_kinds_and_site_overrides(self):
        self.cfg["sites"] = {"7": {"api_prefixes": ["/rpc2/"]}}
        config.validate(self.cfg)
        c = classify.Classifier(self.cfg)
        self.assertEqual(c.kind(0, "example.com", "GET", "/preco/x"), "html")
        self.assertEqual(c.kind(0, "example.com", "GET", "/app.js"), "static")
        self.assertEqual(c.kind(0, "api.example.com", "GET", "/x"), "api")
        self.assertEqual(c.kind(0, "example.com", "POST", "/contato"), "api")
        self.assertEqual(c.kind(0, "example.com", "GET", "/robots.txt"), "other")
        self.assertEqual(c.kind(7, "example.com", "GET", "/rpc2/call"), "api")
        self.assertEqual(c.kind(8, "example.com", "GET", "/rpc2/call"), "html")


class ConfigTest(unittest.TestCase):
    def test_file_env_precedence_and_validation(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "c.toml")
            with open(p, "w") as f:
                f.write('[ui]\ntitle = "Meu Painel"\nlanguage = "en"\n[server]\nport = 9000\n[sites.30]\nname = "Loja"\n')
            os.environ["REQUESTATLAS_PORT"] = "9100"
            cfg = config.load(p)
            self.assertEqual((cfg["ui"]["title"], cfg["ui"]["language"], cfg["server"]["port"]), ("Meu Painel", "en", 9100))
            self.assertEqual(cfg["sites"][30]["name"], "Loja")
            self.assertEqual(cfg["ui"]["pages"], config.DEFAULTS["ui"]["pages"])  # merge preserva defaults
            with open(p, "w") as f:
                f.write('[ui]\npages = ["overview", "nope"]\n')
            with self.assertRaises(config.ConfigError):
                config.load(p)
        finally:
            os.environ.pop("REQUESTATLAS_PORT", None)
            shutil.rmtree(d)


    def test_typed_environment_site_override_and_secret_file(self):
        with tempfile.TemporaryDirectory() as d:
            pwd = os.path.join(d, "password")
            with open(pwd, "w") as f:
                f.write("secret-from-file\n")
            overrides = {
                "REQUESTATLAS__UI__ACCENT": '["#ff8800","#0077bb"]',
                "REQUESTATLAS__UI__SHOW_CAVEATS": "false",
                "REQUESTATLAS__UI__PAGES": '["overview","bots"]',
                "REQUESTATLAS__EVENTS__ITEMS": '[{"ts":"2026-10-05T10:00:00-03:00","title":"Block"}]',
                "REQUESTATLAS__SITES__30__NAME": "Minha loja",
                "REQUESTATLAS__SITES__30__API_PREFIXES": '["/graphql/"]',
                "REQUESTATLAS_AUTH_USER": "admin",
                "REQUESTATLAS_AUTH_PASSWORD_FILE": pwd,
            }
            with patch.dict(os.environ, overrides):
                loaded = config.load(os.devnull)
            self.assertEqual(loaded["ui"]["pages"], ["overview", "bots"])
            self.assertEqual(loaded["ui"]["accent"], ["#ff8800", "#0077bb"])
            self.assertIs(loaded["ui"]["show_caveats"], False)
            self.assertEqual(loaded["sites"][30]["api_prefixes"], ["/graphql/"])
            self.assertEqual(loaded["sites"][30]["name"], "Minha loja")
            self.assertEqual(loaded["server"]["auth_password"], "secret-from-file")
            self.assertEqual(loaded["events"][0]["title"], "Block")
            self.assertNotIn("secret-from-file", str(api.Api(loaded).ui_config()))

    def test_bad_settings_fail_before_server_starts(self):
        for override in (
            {"REQUESTATLAS__UI__ACCENT": '["not a color"]'},
            {"REQUESTATLAS__UI__SHOW_CAVEATS": "not-boolean"},
            {"REQUESTATLAS__UI__LINKS": '[{"label":"Bad","url":"javascript:alert(1)"}]'},
            {"REQUESTATLAS__SERVER__TRUST_X_FORWARDED_FOR": "true"},
            {"REQUESTATLAS__SERVER__ALLOW_NETWORKS": '["not-a-cidr"]'},
            {"REQUESTATLAS__UI__TYPO": "foo"},
            {"REQUESTATLAS__EVENTS__ITEMS": "not-json"},
        ):
            with self.subTest(override=override), patch.dict(os.environ, override):
                with self.assertRaises(config.ConfigError):
                    config.load(os.devnull)

    def test_probe_bypasses_auth_but_detects_stale_collector(self):
        env = Env(server={"auth_user": "admin", "auth_password": "secret",
                          "allow_networks": ["203.0.113.0/24"], "listen": "127.0.0.1"})
        env.cfg["server"]["port"] = 0  # bind ephemeral port only after validating production settings
        try:
            store.connect(config.db_path(env.cfg)).close()
            service = api.Api(env.cfg)
            server = api.make_server(service, env.cfg)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urllib.request.urlopen(address + "/healthz") as res:
                    self.assertEqual((res.status, res.read()), (200, b"ok\n"))
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(address + "/api/config")
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                # A first backfill may exceed the grace window without yet completing a cycle.
                service.started_at -= 601
                service.ingest_thread = thread
                with urllib.request.urlopen(address + "/healthz") as res:
                    self.assertEqual((res.status, res.read()), (200, b"ok\n"))
                service.ingest_thread = None
                with self.assertRaises(urllib.error.HTTPError) as stale:
                    urllib.request.urlopen(address + "/healthz")
                self.assertEqual((stale.exception.code, stale.exception.read()), (503, b"stale\n"))
                stale.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        finally:
            env.close()

    def test_basic_auth_and_untrusted_forwarded_for(self):
        env = Env(server={"auth_user": "admin", "auth_password": "secret",
                          "allow_networks": ["127.0.0.1/32"], "listen": "127.0.0.1",
                          "trust_x_forwarded_for": True,
                          "trusted_proxy_networks": ["10.0.0.0/8"]})
        env.cfg["server"]["port"] = 0
        try:
            store.connect(config.db_path(env.cfg)).close()
            server = api.make_server(api.Api(env.cfg), env.cfg)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/api/config"
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(url)
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                credential = base64.b64encode(b"admin:secret").decode()
                request = urllib.request.Request(url, headers={
                    "Authorization": f"Basic {credential}",
                    "X-Forwarded-For": "203.0.113.77",
                })
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)["title"], "RequestAtlas")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        finally:
            env.close()

    def test_trusted_proxy_uses_nearest_untrusted_xff(self):
        env = Env(server={"listen": "127.0.0.1", "allow_networks": ["203.0.113.0/24"],
                          "trust_x_forwarded_for": True,
                          "trusted_proxy_networks": ["127.0.0.1/32"]})
        env.cfg["server"]["port"] = 0
        try:
            store.connect(config.db_path(env.cfg)).close()
            server = api.make_server(api.Api(env.cfg), env.cfg)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/api/config"
            try:
                forged = urllib.request.Request(url, headers={
                    "X-Forwarded-For": "203.0.113.77, 198.51.100.10"})
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(forged)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                valid = urllib.request.Request(url, headers={
                    "X-Forwarded-For": "198.51.100.10, 203.0.113.77"})
                with urllib.request.urlopen(valid) as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        finally:
            env.close()

class IngestTest(unittest.TestCase):

    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)
        self.cfg = self.env.cfg

    def test_partial_line_rotation_nul_and_idempotency(self):
        log = self.env.log()
        ing = ingest.Ingester(self.cfg)
        self.addCleanup(ing.close)
        with open(log, "w") as f:
            for i in range(100):
                f.write(proxy_line(i))
            f.write(proxy_line(100)[:50])
        ing.run_once()
        self.assertEqual(totals(self.cfg)[0], 100)
        with open(log, "a") as f:
            f.write(proxy_line(100)[50:])
            for i in range(101, 121):
                f.write(proxy_line(i, ua="Mozilla/5.0 (compatible; GPTBot/1.2)", status=403) if i < 106 else proxy_line(i))
            f.write("lixo sem formato\n")
            f.write("\x00" * 64 + proxy_line(121))
        ing.run_once()
        with open(log, "a") as f:  # escrito depois da última leitura: só será visto no .gz
            for i in range(122, 140):
                f.write(proxy_line(i))
        os.rename(log, log + ".1")
        with open(log, "w") as f:
            for i in range(200, 230):
                f.write(proxy_line(i))
        with open(log + ".1", "rb") as src, gzip.open(log + ".1.gz", "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.remove(log + ".1")
        ing.run_once()
        ing.run_once()
        os.rename(log + ".1.gz", log + ".2.gz")
        ing.run_once()
        tot, by_bot, rule = totals(self.cfg)
        self.assertEqual(tot, 100 + 1 + 20 + 1 + 18 + 30)
        self.assertEqual(by_bot.get("GPTBot"), 5)
        self.assertEqual(rule, 5)  # regra do Advanced do proxy host 30 (banco fake)
        c = sqlite3.connect(config.db_path(self.cfg))
        try:
            self.assertEqual(c.execute("SELECT SUM(malformed), SUM(repaired) FROM files").fetchone(), (1, 1))
        finally:
            c.close()

    def test_crash_mid_batch_does_not_double_count(self):
        log = self.env.log()
        with open(log, "w") as f:
            for i in range(50):
                f.write(proxy_line(i))
        ing = ingest.Ingester(self.cfg)
        self.addCleanup(ing.close)
        orig = ing._ingest_batch
        ing._ingest_batch = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        ing.run_once()
        ing._ingest_batch = orig
        ing.run_once()
        self.assertEqual(totals(self.cfg)[0], 50)

    def test_exclude_hosts_and_reindex(self):
        with open(self.env.log(), "w") as f:
            f.write(proxy_line(1, host="painel.local"))
            f.write(proxy_line(2))
        self.cfg["ingest"]["exclude_hosts"] = ["painel.local"]
        ing = ingest.Ingester(self.cfg)
        self.addCleanup(ing.close)
        ing.run_once()
        self.assertEqual(totals(self.cfg)[0], 1)
        ing.reset()
        ing.run_once()
        self.assertEqual(totals(self.cfg)[0], 1)

    def test_log_only_mode_with_configured_domains_and_block_rules(self):
        self.cfg["paths"]["npm_db"] = os.path.join(self.env.root, "missing.sqlite")
        self.cfg["sites"] = {"30": {
            "name": "Demo", "domains": ["example.com", "www.example.com"],
            "ua_rules": [{"pattern": "GPTBot", "status": 403}],
        }}
        config.validate(self.cfg)
        with open(self.env.log(), "w") as file:
            file.write(proxy_line(1, ua="GPTBot/1.2", status=403))
        ing = ingest.Ingester(self.cfg)
        self.addCleanup(ing.close)
        ing.run_once()
        self.assertEqual(totals(self.cfg)[2], 1)
        app = api.Api(self.cfg)
        self.addCleanup(app.close_thread)
        site = next(s for s in app.meta({})["sites"] if s["id"] == 30)
        self.assertEqual((site["type"], site["name"], site["deleted"]), ("log-only", "Demo", False))
        self.assertEqual(site["domains"], ["example.com", "www.example.com"])
        self.assertEqual(app.health({})["rules"][0]["rules"][0]["status"], 403)

    def test_manual_rule_override_replaces_sqlite_advanced_rule(self):
        self.cfg["sites"] = {"30": {"ua_rules": [{"pattern": "ClaudeBot", "status": 403}]}}
        config.validate(self.cfg)
        with open(self.env.log(), "w") as file:
            file.write(proxy_line(1, ua="GPTBot/1.2", status=403))
            file.write(proxy_line(2, ua="ClaudeBot/1.0", status=403))
        ing = ingest.Ingester(self.cfg)
        self.addCleanup(ing.close)
        ing.run_once()
        self.assertEqual(totals(self.cfg)[2], 1)
        app = api.Api(self.cfg)
        self.addCleanup(app.close_thread)
        rule = next(s for s in app.meta({})["sites"] if s["id"] == 30)["ua_rules"]
        self.assertEqual(rule, [{"pattern": "ClaudeBot", "status": 403}])


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env(ui={"default_tz": "America/Sao_Paulo"}, sites={"30": {"name": "Loja"}},
                      events=[{"ts": "2026-10-05T10:30:00-09:00", "site": 30, "bot": "GPTBot", "title": "bloqueio"}])
        with open(cls.env.log(), "w") as f:
            # 02:30 UTC de 06/10 = 23:30 de 05/10 em São Paulo
            f.write(proxy_line(1, ts="05/Oct/2026:17:30:00 -0900"))
            f.write(proxy_line(2, ts="05/Oct/2026:17:50:00 -0900"))
            f.write(proxy_line(3, ua="GPTBot/1.2", status=403, ts="05/Oct/2026:10:40:00 -0900"))
        ing = ingest.Ingester(cls.env.cfg)
        try:
            ing.run_once()
        finally:
            ing.close()
        cls.api = api.Api(cls.env.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.api.close_thread()
        cls.env.close()

    def window(self, **kw):
        base = {"from": str(int(time.mktime((2026, 10, 4, 0, 0, 0, 0, 0, 0)))), "to": str(int(time.mktime((2026, 10, 8, 0, 0, 0, 0, 0, 0))))}
        return {**base, **kw}

    def test_day_buckets_follow_display_timezone(self):
        r_sp = self.api.timeseries(self.window(group="kind", bucket="day", tz="America/Sao_Paulo"))
        r_utc = self.api.timeseries(self.window(group="kind", bucket="day", tz="UTC"))
        nz = lambda r: [v for v in r["series"][0]["data"] if v]
        self.assertEqual(nz(r_sp), [3])          # tudo em 05/10 no horário de Brasília
        self.assertEqual(nz(r_utc), [1, 2])      # 05/10 e 06/10 em UTC

    def test_rule_403_and_site_name_and_filters(self):
        ov = self.api.overview(self.window())
        self.assertEqual(ov["totals"]["s403_rule"], 1)
        meta = self.api.meta({})
        self.assertEqual([s["name"] for s in meta["sites"] if s["id"] == 30], ["Loja"])
        only_403 = self.api.overview(self.window(status="403r"))
        self.assertEqual(only_403["totals"]["total"], 1)
        with self.assertRaises(api.BadRequest):
            self.api.overview(self.window(tz="Mars/Base"))

    def test_events_report_bot_after_event(self):
        ev = self.api.events({"window": "24"})["events"][0]
        self.assertEqual(ev["bot_info"]["after_by_status"], [{"status": 403, "rule": 1, "hits": 1}])


if __name__ == "__main__":
    unittest.main()
