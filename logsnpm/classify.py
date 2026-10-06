"""Classificação de User-Agent (bots) e do tipo de requisição, dirigida pela configuração.

O User-Agent chega já extraído do campo correto do log (o que vem logo após [Sent-to ...]);
nada aqui olha a linha inteira, a URL ou o referrer.
"""
import re

# (nome, grupo, regex aplicada SOMENTE ao User-Agent). O primeiro match vence.
# Grupos são chaves; a interface traduz (ai, search, seo, social, monitoring, scanner, tool).
BOT_RULES = [
    # IA / LLM
    ("GPTBot", "ai", r"GPTBot"),
    ("OAI-SearchBot", "ai", r"OAI-SearchBot"),
    ("ChatGPT-User", "ai", r"ChatGPT-User"),
    ("ClaudeBot", "ai", r"ClaudeBot|Claude-SearchBot"),
    ("Claude-User", "ai", r"Claude-User|Claude-Web|anthropic-ai"),
    ("PerplexityBot", "ai", r"PerplexityBot|Perplexity-User"),
    ("Bytespider", "ai", r"Bytespider|TikTokSpider"),
    ("CCBot", "ai", r"CCBot"),
    ("Amazonbot", "ai", r"Amazonbot"),
    ("Applebot", "ai", r"Applebot"),
    ("Meta-ExternalAgent", "ai", r"meta-externalagent|meta-externalfetcher|FacebookBot"),
    ("Google-Extended/GoogleOther", "ai", r"GoogleOther|Google-Extended|Google-CloudVertexBot"),
    ("cohere-ai", "ai", r"cohere-ai|cohere-training"),
    ("Diffbot", "ai", r"Diffbot"),
    ("YouBot", "ai", r"YouBot"),
    ("Timpibot", "ai", r"Timpibot"),
    ("ImagesiftBot", "ai", r"ImagesiftBot"),
    ("AI2Bot", "ai", r"AI2Bot"),
    ("DuckAssistBot", "ai", r"DuckAssistBot"),
    ("MistralAI-User", "ai", r"MistralAI-User"),
    # Google (ordem: específicos antes do Googlebot)
    ("Mediapartners-Google (AdSense)", "search", r"Mediapartners-Google"),
    ("AdsBot-Google", "search", r"AdsBot-Google"),
    ("Google-InspectionTool", "search", r"Google-InspectionTool"),
    ("Storebot-Google", "search", r"Storebot-Google"),
    ("Googlebot-Image/Video", "search", r"Googlebot-(Image|Video|News)"),
    ("Googlebot", "search", r"Googlebot"),
    ("Google (other)", "search", r"FeedFetcher-Google|APIs-Google|Google-Read-Aloud|Google-Site-Verification|Google Favicon|GoogleProducer|Google-Safety"),
    # Outros buscadores
    ("Bingbot", "search", r"bingbot|BingPreview|msnbot|adidxbot"),
    ("YandexBot", "search", r"Yandex"),
    ("Baiduspider", "search", r"Baiduspider"),
    ("DuckDuckBot", "search", r"DuckDuckBot|DuckDuckGo-Favicons"),
    ("PetalBot", "search", r"PetalBot|AspiegelBot"),
    ("SeznamBot", "search", r"SeznamBot"),
    ("Qwantbot", "search", r"Qwantify|Qwantbot"),
    ("Yahoo Slurp", "search", r"Slurp"),
    ("Mojeek", "search", r"MojeekBot"),
    ("Sogou", "search", r"Sogou"),
    # SEO / marketing
    ("AhrefsBot", "seo", r"AhrefsBot|AhrefsSiteAudit"),
    ("SemrushBot", "seo", r"SemrushBot|SiteAuditBot|SplitSignalBot"),
    ("MJ12bot", "seo", r"MJ12bot"),
    ("DotBot (Moz)", "seo", r"DotBot|rogerbot"),
    ("DataForSeoBot", "seo", r"DataForSeoBot"),
    ("Barkrowler", "seo", r"Barkrowler"),
    ("serpstatbot", "seo", r"serpstatbot"),
    ("BLEXBot", "seo", r"BLEXBot"),
    ("Screaming Frog", "seo", r"Screaming Frog"),
    ("SEO (other)", "seo", r"SeekportBot|Seekport|linkdexbot|MegaIndex|ZoominfoBot|Mail\.RU_Bot|coccocbot|Turnitin|Sidetrade|AwarioBot|Brandwatch|Cliqzbot"),
    # Social / preview de links
    ("facebookexternalhit", "social", r"facebookexternalhit|facebookcatalog"),
    ("WhatsApp", "social", r"WhatsApp"),
    ("Twitterbot", "social", r"Twitterbot"),
    ("LinkedInBot", "social", r"LinkedInBot"),
    ("TelegramBot", "social", r"TelegramBot"),
    ("Slackbot", "social", r"Slackbot|Slack-ImgProxy"),
    ("Discordbot", "social", r"Discordbot"),
    ("Pinterestbot", "social", r"Pinterest"),
    ("Social (other)", "social", r"redditbot|SkypeUriPreview|Iframely|Embedly|vkShare|Snapchat|Viber|Mastodon"),
    # Monitoramento
    ("Uptime Kuma", "monitoring", r"Uptime-Kuma"),
    ("UptimeRobot", "monitoring", r"UptimeRobot"),
    ("Monitoramento (other)", "monitoring", r"Pingdom|StatusCake|Better ?Uptime|Site24x7|Datadog|NewRelicPinger|HetrixTools|Freshping|Checkly|updown\.io|Beszel|Gatus|Zabbix"),
    # Scanners / pesquisa de internet
    ("Censys", "scanner", r"CensysInspect|censys"),
    ("Expanse (Palo Alto)", "scanner", r"Expanse|paloaltonetworks"),
    ("zgrab", "scanner", r"zgrab"),
    ("Nmap/masscan", "scanner", r"Nmap|masscan"),
    ("Scanner (other)", "scanner", r"internet-measurement|InternetMeasurement|Shodan|ModatScanner|Odin|BitSightBot|LeakIX|l9explore|l9tcpid|Nuclei|nikto|sqlmap|WPScan|Keydrop|fasthttp|httpx|Xpanse|abuse\.xmco|CyberRadar|NetSystemsResearch|Hello from Palo Alto|netcraft|Dataprovider|SurdotlyBot|ips-agent|AliyunSecBot"),
    # Ferramentas / bibliotecas HTTP (automação genérica)
    ("curl/wget", "tool", r"^(curl|Wget)/|\bcurl/|\bWget/"),
    ("python-requests/aiohttp", "tool", r"python-requests|aiohttp|python-urllib|Python-urllib|httpx/|python-httpx|Scrapy"),
    ("Go-http-client", "tool", r"Go-http-client|Go http package"),
    ("Node (axios/fetch)", "tool", r"axios/|node-fetch|undici|Node\.js|got \(|^node$"),
    ("Java/okhttp", "tool", r"Java/|okhttp|Apache-HttpClient|Jakarta"),
    ("PHP/Ruby/Perl", "tool", r"GuzzleHttp|PHP/|WordPress/|Ruby|libwww-perl|Faraday"),
    ("Headless Chrome", "tool", r"HeadlessChrome|PhantomJS|Puppeteer|Playwright|Lighthouse|Chrome-Lighthouse"),
    ("Postman/Insomnia", "tool", r"PostmanRuntime|insomnia"),
]

NOT_BOT = "__nonbot__"
EMPTY_UA = "__noua__"
GENERIC = "__generic__"
GROUPS = ["ai", "search", "seo", "social", "monitoring", "scanner", "tool", "custom", "generic", "noua"]

KINDS = ("html", "static", "api", "other")
STATIC_EXT = {
    "js", "mjs", "css", "map", "png", "jpg", "jpeg", "gif", "svg", "ico", "webp", "avif", "bmp",
    "woff", "woff2", "ttf", "otf", "eot", "mp4", "webm", "mp3", "ogg", "wav", "m4a", "pdf",
    "zip", "gz", "wasm", "apk", "dmg", "exe", "csv", "xls", "xlsx", "doc", "docx", "heic",
    "jxl", "tiff", "glb", "gltf", "stl", "3mf", "webmanifest",
}
HTML_EXT = {"", "html", "htm", "php", "asp", "aspx", "jsp", "cgi", "shtml"}
STATIC_PREFIX = ("/_next/static/", "/_next/image", "/static/", "/assets/", "/fonts/", "/images/",
                 "/img/", "/js/", "/css/", "/build/", "/dist/", "/media/", "/favicon", "/apple-touch-icon",
                 "/wp-content/", "/wp-includes/", "/icons/", "/uploads/", "/core/img/", "/core/css/",
                 "/core/js/", "/apps/theming/", "/svg/")
API_PREFIX = ("/api/", "/graphql", "/trpc/", "/rpc/", "/bridge/", "/ocs/", "/remote.php", "/public.php",
              "/index.php/apps/", "/index.php/core/", "/wp-json/", "/xmlrpc.php", "/socket.io/", "/ws/",
              "/websocket", "/v1/", "/v2/", "/v3/", "/webhook", "/hooks/", "/identity/", "/notifications/",
              "/status.php", "/push/", "/oauth", "/auth/", "/token", "/.well-known/caldav", "/.well-known/carddav")
OTHER_EXACT = {"/robots.txt", "/ads.txt", "/app-ads.txt", "/humans.txt", "/security.txt", "/llms.txt",
               "/sitemap.xml", "/sitemap_index.xml", "/feed", "/rss", "/atom.xml", "/manifest.json",
               "/site.webmanifest", "/browserconfig.xml", "/crossdomain.xml"}
_SITEMAP = re.compile(r"^/sitemap[\w\-]*\.xml(\.gz)?$", re.I)


def path_ext(path):
    seg = path.rsplit("/", 1)[-1]
    if "." not in seg:
        return ""
    return seg.rsplit(".", 1)[-1].lower()[:12]


class Classifier:
    def __init__(self, cfg):
        b = cfg["bots"]
        disabled = set(b["disable"])
        custom = [(c["name"], c.get("group", "custom"), c["pattern"]) for c in b["custom"]]
        self.rules = [(n, g, re.compile(rx, re.I)) for n, g, rx in custom + BOT_RULES if n not in disabled]
        self.generic = re.compile(b["generic_pattern"]) if b["generic_pattern"] else None
        c = cfg["classify"]
        self.static_ext = STATIC_EXT | {e.lower().lstrip(".") for e in c["static_extensions_extra"]}
        self.api_prefix = API_PREFIX + tuple(c["api_prefixes_extra"])
        self.api_host_prefix = tuple(c["api_host_prefixes"])
        self.other_exact = OTHER_EXACT | set(c["other_paths_extra"])
        self.sites = {sid: (tuple(s.get("api_hosts", ())), tuple(s.get("api_prefixes", ())))
                      for sid, s in cfg["sites"].items()}

    def bots(self):
        seen, out = set(), []
        for n, g, _ in self.rules:
            if n not in seen:
                seen.add(n)
                out.append((n, g))
        return out + [(GENERIC, "generic"), (EMPTY_UA, "noua"), (NOT_BOT, None)]

    def ua(self, ua):
        """(nome_bot, grupo); NOT_BOT quando nada casou."""
        if not ua or ua == "-":
            return EMPTY_UA, "noua"
        for name, group, rx in self.rules:
            if rx.search(ua):
                return name, group
        if self.generic and self.generic.search(ua):
            return GENERIC, "generic"
        return NOT_BOT, None

    def kind(self, site, host, method, path):
        """html | static | api | other. `path` sem query string."""
        p = path.lower()
        if method == "OPTIONS" or p in self.other_exact or _SITEMAP.match(p):
            return "other"
        if p.startswith("/.well-known/"):
            return "api" if p.startswith(("/.well-known/caldav", "/.well-known/carddav")) else "other"
        api_hosts, api_prefixes = self.sites.get(site, ((), ()))
        if host.startswith(self.api_host_prefix) or host in api_hosts:
            return "api"
        if p == "/api" or p.startswith(self.api_prefix) or (api_prefixes and p.startswith(api_prefixes)):
            return "api"
        if method not in ("GET", "HEAD"):
            return "api"
        ext = path_ext(p)
        if ext in self.static_ext or p.startswith(STATIC_PREFIX):
            return "static"
        if ext in HTML_EXT:
            return "html"
        return "other"

    def describe(self):
        return {"bot_rules": [{"name": n, "group": g, "regex": rx.pattern} for n, g, rx in self.rules],
                "generic_regex": self.generic.pattern if self.generic else None}
