"""Configuração do logsNPM.

Precedência: padrões < TOML < LOGSNPM_* legadas < LOGSNPM__SEÇÃO__CHAVE.
Nas variáveis duplas, números, booleanos, arrays e objetos são JSON; texto sem
aspas permanece texto. Mudanças de classificação exigem ``logsnpm reindex``.
"""
import copy
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULTS = {
    "paths": {
        "log_dir": "/data/logs", "npm_db": "/data/database.sqlite",
        "nginx_custom_dir": "/data/nginx/custom", "data_dir": "/var/lib/logsnpm",
        "geoip_city": "", "geoip_asn": "",
    },
    "ingest": {
        "interval": 60, "retention_days": 180, "batch_lines": 60000,
        "log_globs": ["proxy-host-*_access.log*", "dead-host-*_access.log*",
                      "redirection-host-*_access.log*", "fallback_access.log*", "fallback_http_access.log*"],
        "exclude_hosts": [], "blocked_ip_geo_var": "blocked_ip",
    },
    "server": {
        "listen": "127.0.0.1", "port": 7881, "allow_networks": [],
        "trusted_proxy_networks": [],
        "auth_user": "", "auth_password": "", "cache_ttl": 30,
        "trust_x_forwarded_for": False,
    },
    "ui": {
        "title": "logsNPM", "subtitle": "", "language": "pt-BR", "logo_url": "",
        "accent": ["#818cf8", "#22d3ee"], "default_period": "7d",
        "default_tz": "UTC", "timezones": [],
        "pages": ["overview", "sites", "bots", "status", "pages", "geo", "health"],
        "refresh_seconds": 60, "show_caveats": True, "caveat_text": "",
        "links": [], "colors": {},
    },
    "privacy": {"ipv4_prefix": 24, "ipv6_prefix": 48},
    "classify": {"static_extensions_extra": [], "api_prefixes_extra": [],
                 "api_host_prefixes": ["api.", "api-"], "other_paths_extra": []},
    "bots": {"disable": [], "custom": [],
             "generic_pattern": r"(?i)(bot\b|bot/|crawler|spider|crawl|slurp|fetcher|scanner|scraper|archiver|indexer|monitor\b|preview)"},
    "sites": {}, "events": [],
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
COLOR_KEYS = {"html", "static", "api", "other", "bot", "nonbot", "noua",
              "s1", "s2", "s3", "s4", "s5", "rule", "s429", "palette"}
SITE_KEYS = {"name", "hidden", "domains", "api_prefixes", "api_hosts", "ua_rules"}
BOT_KEYS = {"name", "pattern", "group"}
EVENT_KEYS = {"ts", "title", "detail", "site", "bot"}
LINK_KEYS = {"url", "label", "title"}
HEX_COLOR = re.compile(r"^#[\da-fA-F]{3}(?:[\da-fA-F]{3})?$")


class ConfigError(Exception):
    pass


def _merge(base, over, path=""):
    for key, value in over.items():
        if key not in base:
            raise ConfigError(f"opção desconhecida: {path + key}")
        if isinstance(base[key], dict) and key not in ("sites", "colors"):
            if not isinstance(value, dict):
                raise ConfigError(f"{path + key} deve ser uma tabela")
            _merge(base[key], value, path + key + ".")
        else:
            base[key] = value
    return base


def find_config_path():
    path = os.environ.get("LOGSNPM_CONFIG")
    if path:
        return path
    return next((p for p in CANDIDATES if os.path.exists(p)), None)


def _env_value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = path or find_config_path()
    if path:
        try:
            with open(path, "rb") as f:
                _merge(cfg, tomllib.load(f))
        except FileNotFoundError as exc:
            raise ConfigError(f"arquivo de configuração não encontrado: {path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"TOML inválido em {path}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"não foi possível ler configuração {path}: {exc}") from exc
    for var, (section, key, cast) in ENV.items():
        if var in os.environ:
            try:
                cfg[section][key] = cast(os.environ[var])
            except ValueError as exc:
                raise ConfigError(f"{var}: valor inválido") from exc
    for var, text in sorted(os.environ.items()):
        if not var.startswith("LOGSNPM__"):
            continue
        parts = [part.lower() for part in var.removeprefix("LOGSNPM__").split("__")]
        if len(parts) not in (2, 3) or parts[0] not in DEFAULTS:
            raise ConfigError(f"{var}: use LOGSNPM__SEÇÃO__CHAVE (ou __SITES__ID__CHAVE)")
        section, key = parts[0], parts[-1]
        if section == "sites" and len(parts) == 3:
            if not parts[1].isdigit() or key not in SITE_KEYS:
                raise ConfigError(f"{var}: ID de site ou chave inválida")
            cfg[section].setdefault(parts[1], {})[key] = _env_value(text)
        elif section == "events" and len(parts) == 2 and key == "items":
            cfg["events"] = _env_value(text)
        elif len(parts) == 2 and section not in ("sites", "events") and key in DEFAULTS[section]:
            cfg[section][key] = _env_value(text)
        else:
            raise ConfigError(f"{var}: opção desconhecida")
    password_file = os.environ.get("LOGSNPM_AUTH_PASSWORD_FILE")
    if password_file:
        if cfg["server"]["auth_password"]:
            raise ConfigError("LOGSNPM_AUTH_PASSWORD_FILE não pode ser combinado com auth_password")
        try:
            with open(password_file) as file:
                cfg["server"]["auth_password"] = file.read().rstrip("\r\n")
        except OSError as exc:
            raise ConfigError(f"não foi possível ler LOGSNPM_AUTH_PASSWORD_FILE: {exc}") from exc
    cfg["_path"] = path
    validate(cfg)
    return cfg


def _table(value, name, allowed):
    if not isinstance(value, dict):
        raise ConfigError(f"{name} deve ser uma tabela")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{name}: opção desconhecida: {', '.join(sorted(map(str, unknown)))}")


def _strings(value, name):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{name} deve ser uma lista de textos")


def _integer(value, name, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ConfigError(f"{name} deve ser inteiro entre {lo} e {hi}")


def _color(value, name):
    if not isinstance(value, str) or not HEX_COLOR.fullmatch(value):
        raise ConfigError(f"{name} deve ser cor hexadecimal (#RGB ou #RRGGBB)")


def validate(cfg):
    _table(cfg, "config", set(DEFAULTS) | {"_path"})
    for section, defaults in DEFAULTS.items():
        if isinstance(defaults, dict) and section not in ("sites",):
            _table(cfg[section], section, set(defaults))
    for section in ("paths", "ingest", "server", "ui", "privacy", "classify", "bots"):
        for key, default in DEFAULTS[section].items():
            value = cfg[section][key]
            if type(default) is bool and type(value) is not bool:
                raise ConfigError(f"{section}.{key} deve ser booleano (true/false)")
            if type(default) is str and not isinstance(value, str):
                raise ConfigError(f"{section}.{key} deve ser texto")
            if type(default) is int and type(value) is not int:
                raise ConfigError(f"{section}.{key} deve ser inteiro")
            if isinstance(default, list) and not isinstance(value, list):
                raise ConfigError(f"{section}.{key} deve ser lista")
            if isinstance(default, dict) and not isinstance(value, dict):
                raise ConfigError(f"{section}.{key} deve ser tabela")
    ui, srv = cfg["ui"], cfg["server"]
    if not ui["language"]:
        raise ConfigError("ui.language deve ser código de idioma (pt-BR, en, …)")
    if ui["default_period"] not in ("24h", "7d", "30d", "all"):
        raise ConfigError("ui.default_period deve ser 24h, 7d, 30d ou all")
    try:
        ZoneInfo(ui["default_tz"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("ui.default_tz deve ser fuso IANA válido") from exc
    for entry in ui["timezones"]:
        if isinstance(entry, str):
            zone = entry
        elif isinstance(entry, dict) and set(entry) <= {"id", "label"} and isinstance(entry.get("id"), str) and isinstance(entry.get("label", ""), str):
            zone = entry["id"]
        else:
            raise ConfigError("ui.timezones: use fusos IANA ou { id, label }")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"ui.timezones: fuso inválido: {zone}") from exc
    known_pages = {"overview", "sites", "bots", "status", "pages", "geo", "health"}
    _strings(ui["pages"], "ui.pages")
    if not ui["pages"] or len(ui["pages"]) != len(set(ui["pages"])) or set(ui["pages"]) - known_pages:
        raise ConfigError(f"ui.pages: opções sem repetição: {', '.join(sorted(known_pages))}")
    _strings(ui["accent"], "ui.accent")
    if not 1 <= len(ui["accent"]) <= 2:
        raise ConfigError("ui.accent deve conter uma ou duas cores")
    for color in ui["accent"]:
        _color(color, "ui.accent")
    _table(ui["colors"], "ui.colors", COLOR_KEYS)
    for key, value in ui["colors"].items():
        if key == "palette":
            _strings(value, "ui.colors.palette")
            if not value:
                raise ConfigError("ui.colors.palette não pode ser vazia")
            for color in value:
                _color(color, "ui.colors.palette")
        else:
            _color(value, f"ui.colors.{key}")
    _strings(srv["trusted_proxy_networks"], "server.trusted_proxy_networks")
    if srv["trust_x_forwarded_for"] and not srv["trusted_proxy_networks"]:
        raise ConfigError("server.trust_x_forwarded_for exige server.trusted_proxy_networks")
    for net in srv["trusted_proxy_networks"]:
        try:
            ipaddress.ip_network(net, strict=False)
        except ValueError as exc:
            raise ConfigError(f"server.trusted_proxy_networks: rede inválida: {net}") from exc
    for item in ui["links"]:
        _table(item, "ui.links", LINK_KEYS)
        if not all(isinstance(v, str) for v in item.values()) or not item.get("label") or not item.get("url") or not re.match(r"^(https?://|/[^/])", item["url"]):
            raise ConfigError("ui.links exige label e URL http(s) ou caminho relativo à raiz")
    if ui["logo_url"] and not re.match(r"^(https?://|/[^/])", ui["logo_url"]):
        raise ConfigError("ui.logo_url deve ser URL http(s) ou caminho relativo à raiz")
    _integer(ui["refresh_seconds"], "ui.refresh_seconds", 0, 86400)
    _integer(srv["port"], "server.port", 1, 65535)
    _integer(srv["cache_ttl"], "server.cache_ttl", 0, 3600)
    if bool(srv["auth_user"]) != bool(srv["auth_password"]):
        raise ConfigError("server.auth_user e server.auth_password devem ser definidos juntos")
    _strings(srv["allow_networks"], "server.allow_networks")
    for net in srv["allow_networks"]:
        try:
            ipaddress.ip_network(net, strict=False)
        except ValueError as exc:
            raise ConfigError(f"server.allow_networks: rede inválida: {net}") from exc
    _integer(cfg["ingest"]["interval"], "ingest.interval", 1, 86400)
    _integer(cfg["ingest"]["retention_days"], "ingest.retention_days", 1, 3650)
    _integer(cfg["ingest"]["batch_lines"], "ingest.batch_lines", 1, 1000000)
    for section, keys in (("ingest", ("log_globs", "exclude_hosts")),
                          ("classify", ("static_extensions_extra", "api_prefixes_extra", "api_host_prefixes", "other_paths_extra")),
                          ("bots", ("disable",))):
        for key in keys:
            _strings(cfg[section][key], f"{section}.{key}")
    for bot in cfg["bots"]["custom"]:
        _table(bot, "bots.custom", BOT_KEYS)
        if not isinstance(bot.get("name"), str) or not isinstance(bot.get("pattern"), str) or not isinstance(bot.get("group", "custom"), str):
            raise ConfigError("bots.custom exige name/pattern e group opcional")
        try:
            re.compile(bot["pattern"])
        except re.error as exc:
            raise ConfigError(f"bots.custom.{bot['name']}: regex inválida: {exc}") from exc
    if cfg["bots"]["generic_pattern"]:
        try:
            re.compile(cfg["bots"]["generic_pattern"])
        except re.error as exc:
            raise ConfigError(f"bots.generic_pattern: regex inválida: {exc}") from exc
    _integer(cfg["privacy"]["ipv4_prefix"], "privacy.ipv4_prefix", 8, 32)
    _integer(cfg["privacy"]["ipv6_prefix"], "privacy.ipv6_prefix", 16, 128)
    if not isinstance(cfg["events"], list):
        raise ConfigError("events deve ser uma lista de tabelas")
    for event in cfg["events"]:
        _table(event, "events", EVENT_KEYS)
        stamp = event.get("ts")
        try:
            parsed = stamp if isinstance(stamp, dt.datetime) else dt.datetime.fromisoformat(stamp)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("missing offset")
        except (ValueError, TypeError) as exc:
            raise ConfigError("events.ts deve ter offset UTC (ex.: 2026-10-05T10:00:00-03:00)") from exc
        if not isinstance(event.get("title"), str) or not event["title"]:
            raise ConfigError("events.title deve ser texto não vazio")
        for key in ("bot", "detail"):
            if key in event and not isinstance(event[key], str):
                raise ConfigError(f"events.{key} deve ser texto")
        if "site" in event and type(event["site"]) is not int:
            raise ConfigError("events.site deve ser ID numérico")
    sites = {}
    _table(cfg["sites"], "sites", set(cfg["sites"]))
    for key, site in cfg["sites"].items():
        try:
            sid = int(key)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"sites.{key}: use ID numérico") from exc
        _table(site, f"sites.{sid}", SITE_KEYS)
        for name, value in site.items():
            if name in ("domains", "api_prefixes", "api_hosts"):
                _strings(value, f"sites.{sid}.{name}")
            elif name == "ua_rules":
                if not isinstance(value, list):
                    raise ConfigError(f"sites.{sid}.ua_rules deve ser lista de regras")
                for rule in value:
                    _table(rule, f"sites.{sid}.ua_rules", {"pattern", "status"})
                    if not isinstance(rule.get("pattern"), str) or type(rule.get("status")) is not int:
                        raise ConfigError(f"sites.{sid}.ua_rules exige pattern (texto) e status (número)")
                    _integer(rule["status"], f"sites.{sid}.ua_rules.status", 400, 599)
                    try:
                        re.compile(rule["pattern"], re.I)
                    except re.error as exc:
                        raise ConfigError(f"sites.{sid}.ua_rules.pattern: regex inválida: {exc}") from exc
            elif name == "hidden" and type(value) is not bool:
                raise ConfigError(f"sites.{sid}.hidden deve ser booleano")
            elif name == "name" and not isinstance(value, str):
                raise ConfigError(f"sites.{sid}.name deve ser texto")
        sites[sid] = site
    cfg["sites"] = sites


def db_path(cfg):
    return os.path.join(cfg["paths"]["data_dir"], "logsnpm.db")


def rules_fingerprint(cfg):
    """Hash de opções de classificação (exige reindex se mudar)."""
    keys = {"classify": cfg["classify"], "bots": cfg["bots"], "privacy": cfg["privacy"],
            "exclude_hosts": cfg["ingest"]["exclude_hosts"],
            "sites": {str(k): {kk: vv for kk, vv in v.items() if kk in ("api_prefixes", "api_hosts", "ua_rules")}
                      for k, v in cfg["sites"].items()}}
    return hashlib.sha1(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:12]
