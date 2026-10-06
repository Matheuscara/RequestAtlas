"""Testes do logsNPM (stdlib unittest): `python -m unittest discover -s tests`."""
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import logging
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import Env, proxy_line, standard_line  # noqa: E402
from logsnpm import api, classify, config, ingest  # noqa: E402

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
            os.environ["LOGSNPM_PORT"] = "9100"
            cfg = config.load(p)
            self.assertEqual((cfg["ui"]["title"], cfg["ui"]["language"], cfg["server"]["port"]), ("Meu Painel", "en", 9100))
            self.assertEqual(cfg["sites"][30]["name"], "Loja")
            self.assertEqual(cfg["ui"]["pages"], config.DEFAULTS["ui"]["pages"])  # merge preserva defaults
            with open(p, "w") as f:
                f.write('[ui]\npages = ["overview", "nope"]\n')
            with self.assertRaises(config.ConfigError):
                config.load(p)
        finally:
            os.environ.pop("LOGSNPM_PORT", None)
            shutil.rmtree(d)


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.cfg = self.env.cfg

    def tearDown(self):
        self.env.close()

    def test_partial_line_rotation_nul_and_idempotency(self):
        log = self.env.log()
        ing = ingest.Ingester(self.cfg)
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
        self.assertEqual(c.execute("SELECT SUM(malformed), SUM(repaired) FROM files").fetchone(), (1, 1))

    def test_crash_mid_batch_does_not_double_count(self):
        log = self.env.log()
        with open(log, "w") as f:
            for i in range(50):
                f.write(proxy_line(i))
        ing = ingest.Ingester(self.cfg)
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
        ing.run_once()
        self.assertEqual(totals(self.cfg)[0], 1)
        ing.reset()
        ing.run_once()
        self.assertEqual(totals(self.cfg)[0], 1)


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
        ingest.Ingester(cls.env.cfg).run_once()
        cls.api = api.Api(cls.env.cfg)

    @classmethod
    def tearDownClass(cls):
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
