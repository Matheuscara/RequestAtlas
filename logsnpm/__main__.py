"""logsNPM — painel de análise dos access logs do Nginx Proxy Manager.

Uso:
  python -m logsnpm serve          coleta incremental (a cada ingest.interval s) + painel/API HTTP
  python -m logsnpm ingest         um ciclo de ingestão e sai
  python -m logsnpm reindex        apaga os agregados e reprocessa todos os logs (após mudar regras)
  python -m logsnpm check          valida a configuração e mostra o que foi encontrado
Opções: --config CAMINHO (ou $LOGSNPM_CONFIG)
"""
import argparse
import json
import logging
import os
import sys
import threading
import time

from . import __version__, api, config, ingest, store


def ingest_loop(cfg, stop):
    ing = ingest.Ingester(cfg)
    last_prune = 0
    try:
        while not stop.is_set():
            try:
                n, secs = ing.run_once()
                if n:
                    logging.info("ciclo: %d linhas em %.1fs", n, secs)
                if time.time() - last_prune > 86400:
                    ing.prune()
                    last_prune = time.time()
            except Exception:
                logging.exception("ciclo de ingestão falhou")
            stop.wait(int(cfg["ingest"]["interval"]))
    finally:
        ing.close()


def cmd_check(cfg):
    p = cfg["paths"]
    sites = store.read_npm_sites(p["npm_db"])
    ing = ingest.Ingester.__new__(ingest.Ingester)
    ing.log_dir, ing.globs = p["log_dir"], cfg["ingest"]["log_globs"]
    files = ingest.Ingester.candidates(ing)
    report = {
        "config": cfg.get("_path") or "(somente defaults/env)",
        "log_dir": {"path": p["log_dir"], "exists": os.path.isdir(p["log_dir"]), "access_logs": len(files)},
        "npm_db": {"path": p["npm_db"], "exists": os.path.exists(p["npm_db"]),
                   "readable": os.access(p["npm_db"], os.R_OK),
                   "mode": "sqlite" if os.path.isfile(p["npm_db"]) else "log-only",
                   "proxy_hosts": len(sites), "ua_block_rules": sum(len(s["ua_rules"]) for s in sites)},
        "geoip": {k: (p[k], os.path.exists(p[k]) if p[k] else None) for k in ("geoip_city", "geoip_asn")},
        "blocked_ips": len(store.read_blocked_ips(p["nginx_custom_dir"], cfg["ingest"]["blocked_ip_geo_var"])),
        "data_dir": p["data_dir"],
        "listen": f'{cfg["server"]["listen"]}:{cfg["server"]["port"]}',
        "auth": bool(cfg["server"]["auth_user"]),
        "allow_networks": cfg["server"]["allow_networks"] or "todas",
        "events": len(cfg["events"]), "custom_bots": len(cfg["bots"]["custom"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["log_dir"]["exists"] and report["npm_db"]["exists"] and report["npm_db"]["readable"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="logsnpm", description="Painel de análise dos logs do Nginx Proxy Manager")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "ingest", "reindex", "check", "version"])
    ap.add_argument("--config", help="arquivo TOML (padrão: $LOGSNPM_CONFIG, ./logsnpm.toml, /etc/logsnpm/logsnpm.toml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if a.command == "version":
        print(__version__)
        return 0
    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(f"erro de configuração: {e}", file=sys.stderr)
        return 2
    if a.command == "check":
        return cmd_check(cfg)
    if a.command in ("ingest", "reindex"):
        ing = ingest.Ingester(cfg)
        try:
            if a.command == "reindex":
                ing.reset()
            n, secs = ing.run_once()
            print(f"{n} linhas em {secs:.1f}s")
        finally:
            ing.close()
        return 0
    stop = threading.Event()
    store.connect(config.db_path(cfg)).close()  # garante o esquema antes da API abrir read-only
    worker = threading.Thread(target=ingest_loop, args=(cfg, stop), daemon=True, name="ingest")
    worker.start()
    application = api.Api(cfg)
    application.ingest_thread = worker
    srv = api.make_server(application, cfg)
    logging.info("logsNPM %s em http://%s:%s (config: %s)", __version__, cfg["server"]["listen"],
                 cfg["server"]["port"], cfg.get("_path") or "defaults")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.server_close()
        worker.join(timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
