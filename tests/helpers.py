import copy
import json
import os
import shutil
import sqlite3
import tempfile

from requestatlas import config


def proxy_line(i, ua="Mozilla/5.0 Chrome/129", status=200, path=None, host="example.com", ip=None,
               ref="https://bot.example/robots", upstream=None, method="GET", ts=None):
    ts = ts or f"05/Oct/2026:10:{i // 60 % 60:02d}:{i % 60:02d} -0900"
    up = upstream if upstream is not None else str(status)
    path = path or f"/licitacao/botucatu-{i}"
    ip = ip or f"74.7.242.{i % 250}"
    return (f'[{ts}] - {up} {status} - {method} https {host} "{path}" [Client {ip}] [Length 100] [Gzip -] '
            f'[Sent-to 192.168.4.45] "{ua}" "{ref}"\n')


def standard_line(ua="curl/8.0", status=200, host="unknown.example", path="/"):
    return (f'[05/Oct/2026:10:00:00 -0900] {status} - GET http {host} "{path}" [Client 85.11.182.77] '
            f'[Length 568] [Gzip 1.86] "{ua}" "-"\n')


class Env:
    """Diretório temporário com logs, banco fake do NPM e config."""

    def __init__(self, **over):
        self.root = tempfile.mkdtemp(prefix="requestatlas-test-")
        self.logs = os.path.join(self.root, "logs")
        self.custom = os.path.join(self.root, "custom")
        os.makedirs(self.logs)
        os.makedirs(self.custom)
        self.npm_db = os.path.join(self.root, "database.sqlite")
        c = sqlite3.connect(self.npm_db)
        c.execute("CREATE TABLE proxy_host(id INTEGER, domain_names TEXT, forward_scheme TEXT, forward_host TEXT,"
                  " forward_port INTEGER, enabled INTEGER, is_deleted INTEGER, certificate_id INTEGER,"
                  " ssl_forced INTEGER, advanced_config TEXT)")
        c.execute("INSERT INTO proxy_host VALUES(30, ?, 'http', '10.0.0.5', 8080, 1, 0, 1, 1, ?)",
                  (json.dumps(["example.com", "www.example.com"]), "if ($http_user_agent ~* GPTBot) { return 403; }"))
        c.commit()
        c.close()
        with open(os.path.join(self.custom, "http_top.conf"), "w") as f:
            f.write("geo $blocked_ip {\n  default 0;\n  45.9.168.93 1;\n}\n")
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["_path"] = None
        self.cfg["paths"].update(log_dir=self.logs, npm_db=self.npm_db, nginx_custom_dir=self.custom,
                                 data_dir=os.path.join(self.root, "data"))
        for sec, vals in over.items():
            if isinstance(vals, dict) and sec != "sites":
                self.cfg[sec].update(vals)
            else:
                self.cfg[sec] = vals
        config.validate(self.cfg)

    def log(self, name="proxy-host-30_access.log"):
        return os.path.join(self.logs, name)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)
