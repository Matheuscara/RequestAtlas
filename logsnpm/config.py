"""Configuração do logsNPM.

Ordem de precedência: defaults embutidos < arquivo TOML < variáveis de ambiente.
Arquivo: $LOGSNPM_CONFIG ou ./logsnpm.toml ou /etc/logsnpm/logsnpm.toml.
"""
import copy
import hashlib
import json
import os
import tomllib

DEFAULTS = {
    "paths": {
        "log_dir": "/data/logs",
        "npm_db": "/data/database.sqlite",
        "nginx_custom_dir": "/data/nginx/custom",
        "data_dir": "/var/lib/logsnpm",
        "geoip_city": "",
        "geoip_asn": "",
    },
    "ingest": {
        "interval": 60,
        "retention_days": 180,
        "batch_lines": 60000,
        "log_globs": [
            "proxy-host-*_access.log*",
            "dead-host-*_access.log*",
            "redirection-host-*_access.log*",
            "fallback_access.log*",
            "fallback_http_access.log*",
        ],
        "exclude_hosts": [],
        "blocked_ip_geo_var": "blocked_ip",
    },
    "server": {
        "listen": "0.0.0.0",
        "port": 7881,
        "allow_networks": [],
        "auth_user": "",
        "auth_password": "",
        "cache_ttl": 30,
        "trust_x_forwarded_for": False,
    },
    "ui": {
        "title": "logsNPM",
        "subtitle": "",
        "language": "pt-BR",
        "logo_url": "",
        "accent": ["#818cf8", "#22d3ee"],
        "default_period": "7d",
        "default_tz": "UTC",
        "timezones": [],
        "pages": ["overview", "sites", "bots", "status", "pages", "geo", "health"],
        "refresh_seconds": 60,
        "show_caveats": True,
        "caveat_text": "",
        "links": [],
        "colors": {},
    },
    "privacy": {
        "ipv4_prefix": 24,
        "ipv6_prefix": 48,
    },
    "classify": {
        "static_extensions_extra": [],
        "api_prefixes_extra": [],
        "api_host_prefixes": ["api.", "api-"],
        "other_paths_extra": [],
    },
    "bots": {
        "disable": [],
        "custom": [],
        "generic_pattern": r"(?i)(bot\b|bot/|crawler|spider|crawl|slurp|fetcher|scanner|scraper|archiver|indexer|monitor\b|preview)",
    },
    "sites": {},
    "events": [],
}

ENV = {
    "LOGSNPM_LOG_DIR": ("paths", "log_dir", str),
    "LOGSNPM_NPM_DB": ("paths", "npm_db", str),
    "LOGSNPM_NGINX_CUSTOM_DIR": ("paths", "nginx_custom_dir", str),
    "LOGSNPM_DATA_DIR": ("paths", "data_dir", str),
    "LOGSNPM_GEOIP_CITY": ("paths", "geoip_city", str),
    "LOGSNPM_GEOIP_ASN": ("paths", "geoip_asn", str),
    "LOGSNPM_LISTEN": ("server", "listen", str),
    "LOGSNPM_PORT": ("server", "port", int),
    "LOGSNPM_AUTH_USER": ("server", "auth_user", str),
    "LOGSNPM_AUTH_PASSWORD": ("server", "auth_password", str),
    "LOGSNPM_LANGUAGE": ("ui", "language", str),
    "LOGSNPM_TITLE": ("ui", "title", str),
    "LOGSNPM_DEFAULT_TZ": ("ui", "default_tz", str),
}

CANDIDATES = ("logsnpm.toml", "/etc/logsnpm/logsnpm.toml")


class ConfigError(Exception):
    pass


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "sites":
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def find_config_path():
    p = os.environ.get("LOGSNPM_CONFIG")
    if p:
        return p
    for c in CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = path or find_config_path()
    if path:
        try:
            with open(path, "rb") as f:
                _merge(cfg, tomllib.load(f))
        except FileNotFoundError:
            raise ConfigError(f"arquivo de configuração não encontrado: {path}")
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"TOML inválido em {path}: {e}")
    for var, (sec, key, typ) in ENV.items():
        if os.environ.get(var):
            cfg[sec][key] = typ(os.environ[var])
    cfg["_path"] = path
    validate(cfg)
    return cfg


def validate(cfg):
    ui = cfg["ui"]
    if not isinstance(ui["language"], str) or not ui["language"]:
        raise ConfigError("ui.language deve ser um código de idioma (pt-BR, en, …)")
    if ui["default_period"] not in ("24h", "7d", "30d", "all"):
        raise ConfigError("ui.default_period deve ser 24h, 7d, 30d ou all")
    known = {"overview", "sites", "bots", "status", "pages", "geo", "health"}
    bad = set(ui["pages"]) - known
    if bad or not ui["pages"]:
        raise ConfigError(f"ui.pages inválido: {sorted(bad)}; opções: {sorted(known)}")
    if bool(cfg["server"]["auth_user"]) != bool(cfg["server"]["auth_password"]):
        raise ConfigError("server.auth_user e server.auth_password devem ser definidos juntos")
    for b in cfg["bots"]["custom"]:
        if not {"name", "pattern"} <= set(b):
            raise ConfigError("cada [[bots.custom]] precisa de name e pattern")
    for e in cfg["events"]:
        if not {"ts", "title"} <= set(e):
            raise ConfigError("cada [[events]] precisa de ts e title")
    p = cfg["privacy"]
    if not (8 <= p["ipv4_prefix"] <= 32 and 16 <= p["ipv6_prefix"] <= 128):
        raise ConfigError("privacy.ipv4_prefix 8–32 e ipv6_prefix 16–128")
    sites = {}
    for k, v in cfg["sites"].items():
        try:
            sites[int(k)] = v
        except ValueError:
            raise ConfigError(f"sites.{k}: a chave deve ser o ID numérico do proxy host")
    cfg["sites"] = sites


def db_path(cfg):
    return os.path.join(cfg["paths"]["data_dir"], "logsnpm.db")


def rules_fingerprint(cfg):
    """Hash das opções que mudam a classificação na ingestão (exige reindex se mudar)."""
    keys = {
        "classify": cfg["classify"], "bots": cfg["bots"], "privacy": cfg["privacy"],
        "exclude_hosts": cfg["ingest"]["exclude_hosts"],
        "sites": {str(k): {kk: vv for kk, vv in v.items() if kk in ("api_prefixes", "api_hosts")} for k, v in cfg["sites"].items()},
    }
    return hashlib.sha1(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:12]
