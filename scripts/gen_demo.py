#!/usr/bin/env python3
"""Gera logs fictícios no formato do Nginx Proxy Manager + um banco do NPM mínimo.

Uso: python scripts/gen_demo.py /tmp/logsnpm-demo [--days 14]
Depois: LOGSNPM_LOG_DIR=/tmp/logsnpm-demo/logs LOGSNPM_NPM_DB=/tmp/logsnpm-demo/database.sqlite \
        LOGSNPM_DATA_DIR=/tmp/logsnpm-demo/data python -m logsnpm serve

Inclui: crawler de IA agressivo que é bloqueado no meio do período (403 por regra), buscadores,
scanners, um pico de 5xx, rotação semanal em .gz e uma linha malformada.
"""
import argparse
import datetime as dt
import gzip
import json
import os
import random
import sqlite3

SITES = [
    # id, domínios, upstream, peso, caminhos
    (1, ["shop.example.com", "www.shop.example.com"], "10.0.0.10:3000", 5,
     ["/", "/produtos", "/produto/{slug}", "/carrinho", "/categoria/{cat}", "/busca"]),
    (2, ["blog.example.com"], "10.0.0.11:2368", 3, ["/", "/post/{slug}", "/tag/{cat}", "/sobre"]),
    (3, ["api.example.com"], "10.0.0.12:8080", 4, ["/v1/orders", "/v1/products/{n}", "/v1/health", "/v1/auth/token"]),
    (4, ["cloud.example.com"], "10.0.0.20:443", 2, ["/remote.php/dav/files/admin/", "/ocs/v2.php/apps/notifications", "/index.php/apps/files/"]),
    (5, ["status.example.com"], "10.0.0.30:3001", 1, ["/", "/api/status-page/heartbeat/main"]),
]
STATIC = ["/assets/app.{h}.js", "/assets/style.{h}.css", "/img/{slug}.webp", "/favicon.ico", "/fonts/inter.woff2"]
BROWSERS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
]
BOTS = [
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot", "20.171.207.{n}"),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "66.249.66.{n}"),
    ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "40.77.167.{n}"),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "160.79.104.{n}"),
    ("Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)", "54.36.148.{n}"),
    ("Mozilla/5.0 (compatible; CensysInspect/1.1; +https://about.censys.io/)", "167.94.138.{n}"),
    ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", "69.171.230.{n}"),
    ("curl/8.5.0", "203.0.113.{n}"),
]
PUBLIC_NETS = ["177.{a}.{b}.{c}", "189.{a}.{b}.{c}", "201.{a}.{b}.{c}", "45.{a}.{b}.{c}", "91.{a}.{b}.{c}", "2804:14c:{a}:{b}::{c}"]
WORDS = ["cadeira", "mesa", "notebook", "camiseta", "tenis", "fone", "monitor", "teclado", "mochila", "relogio"]
SCANS = ["/wp-login.php", "/.env", "/xmlrpc.php", "/admin/config.php", "/.git/config", "/phpmyadmin/"]


def fill(p, r):
    return (p.replace("{slug}", f"{r.choice(WORDS)}-{r.randint(1, 900)}").replace("{cat}", r.choice(WORDS))
            .replace("{n}", str(r.randint(1, 5000))).replace("{h}", f"{r.getrandbits(24):06x}"))


def ip_of(tmpl, r):
    return tmpl.format(n=r.randint(1, 250), a=r.randint(0, 255), b=r.randint(0, 255), c=r.randint(1, 254))


def line(ts, site_domain, upstream, status, method, path, ip, ua, ref, length, up_status=None):
    up = up_status if up_status is not None else (str(status) if status not in (403, 444) else "-")
    return (f"[{ts.strftime('%d/%b/%Y:%H:%M:%S')} -0300] - {up} {status} - {method} https {site_domain} \"{path}\" "
            f"[Client {ip}] [Length {length}] [Gzip -] [Sent-to {upstream.split(':')[0]}] \"{ua}\" \"{ref}\"\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    r = random.Random(a.seed)
    logs = os.path.join(a.out, "logs")
    os.makedirs(logs, exist_ok=True)
    tz = dt.timezone(dt.timedelta(hours=-3))
    now = dt.datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    start = now - dt.timedelta(days=a.days)
    block_at = now - dt.timedelta(days=3, hours=5)
    outage = (now - dt.timedelta(days=6, hours=2), now - dt.timedelta(days=6, hours=1))
    per_site = {s[0]: [] for s in SITES}
    t = start
    while t < now:
        hour_factor = 0.35 + 0.65 * max(0.0, 1 - abs(t.hour - 15) / 10)
        for sid, domains, upstream, weight, paths in SITES:
            n_req = int(weight * 22 * hour_factor * r.uniform(0.7, 1.3))
            for _ in range(n_req):
                ts = t + dt.timedelta(seconds=r.randint(0, 3599))
                dom = r.choice(domains)
                ip = ip_of(r.choice(PUBLIC_NETS), r)
                ua = r.choice(BROWSERS)
                ref = r.choice(["-", "-", f"https://{dom}/", "https://www.google.com/", "https://t.co/x", "https://duckduckgo.com/"])
                if sid == 3:
                    method = r.choice(["GET", "GET", "POST"])
                    path, status = fill(r.choice(paths), r), r.choice([200] * 18 + [201, 401, 404])
                    ua = r.choice(["okhttp/4.12.0", "axios/1.7.4", ua])
                elif sid == 5:
                    method, path, status, ua = "GET", fill(r.choice(paths), r), 200, "Uptime-Kuma/1.23.13"
                else:
                    method = "GET"
                    if r.random() < 0.45:
                        path = fill(r.choice(STATIC), r)
                    else:
                        path = fill(r.choice(paths), r)
                    status = r.choice([200] * 30 + [301, 304, 304, 404])
                up = None
                if outage[0] <= ts < outage[1] and sid == 1:
                    status, up = r.choice([502, 502, 504]), r.choice(["502", "504"])
                per_site[sid].append((ts, line(ts, dom, upstream, status, method, path, ip, ua, ref, r.randint(200, 90000), up)))
            # bots
            for ua, net in BOTS:
                rate = 1.5
                if "GPTBot" in ua and sid in (1, 2):
                    rate = 55 * hour_factor + 20
                if sid in (3, 5):
                    rate = 0.2
                for _ in range(int(rate * r.uniform(0.5, 1.5))):
                    ts = t + dt.timedelta(seconds=r.randint(0, 3599))
                    dom = domains[0]
                    if "Censys" in ua or "curl" in ua:
                        path, status = r.choice(SCANS), 404
                    else:
                        path, status = fill(r.choice(paths), r), 200
                    if "GPTBot" in ua and sid == 1 and ts >= block_at:
                        status = 403
                    per_site[sid].append((ts, line(ts, dom, upstream, status, "GET", path, ip_of(net, r), ua, "-", r.randint(150, 60000))))
        t += dt.timedelta(hours=1)

    # grava: semana atual no arquivo ativo, semanas anteriores em .1.gz, .2.gz...
    for sid, rows in per_site.items():
        rows.sort()
        weeks = {}
        for ts, ln in rows:
            k = (now - ts).days // 7
            weeks.setdefault(k, []).append(ln)
        base = os.path.join(logs, f"proxy-host-{sid}_access.log")
        for k, lines in weeks.items():
            if k == 0:
                with open(base, "w") as f:
                    f.writelines(lines)
                    if sid == 1:
                        f.write("isto não é uma linha de log válida\n")
            else:
                with gzip.open(f"{base}.{k}.gz", "wt") as f:
                    f.writelines(lines)

    dbp = os.path.join(a.out, "database.sqlite")
    if os.path.exists(dbp):
        os.remove(dbp)
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE proxy_host(id INTEGER, domain_names TEXT, forward_scheme TEXT, forward_host TEXT, forward_port INTEGER,"
              " enabled INTEGER, is_deleted INTEGER, certificate_id INTEGER, ssl_forced INTEGER, advanced_config TEXT)")
    for sid, domains, upstream, _, _ in SITES:
        host, port = upstream.split(":")
        adv = "if ($http_user_agent ~* GPTBot) { return 403; }" if sid == 1 else ""
        c.execute("INSERT INTO proxy_host VALUES(?,?,?,?,?,1,0,1,1,?)", (sid, json.dumps(domains), "http", host, int(port), adv))
    c.execute("INSERT INTO proxy_host VALUES(6, ?, 'http', '10.0.0.40', 8096, 0, 0, 0, 0, '')", (json.dumps(["media.example.com"]),))
    c.commit()
    c.close()
    with open(os.path.join(a.out, "logsnpm.toml"), "w") as f:
        f.write(f"""[paths]
log_dir = "{logs}"
npm_db = "{dbp}"
nginx_custom_dir = ""
data_dir = "{os.path.join(a.out, 'data')}"

[server]
listen = "127.0.0.1"
port = 7881

[ui]
subtitle = "demo"
default_tz = "America/Sao_Paulo"
timezones = ["America/Sao_Paulo", "UTC"]

[sites.1]
name = "Loja"

[[events]]
ts = {block_at.isoformat()}
site = 1
bot = "GPTBot"
title = "GPTBot bloqueado (403)"
detail = "Regra no Advanced do proxy host 1."
""")
    total = sum(len(v) for v in per_site.values())
    print(f"{total} linhas em {logs}\nconfig: {os.path.join(a.out, 'logsnpm.toml')}\n"
          f"rode: python -m logsnpm serve --config {os.path.join(a.out, 'logsnpm.toml')}")


if __name__ == "__main__":
    main()
