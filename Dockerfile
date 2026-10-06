FROM python:3.12-slim

# Usuário fixo sem privilégios (10001:10001). O Compose pode trocar UID:GID em tempo de execução
# (`user:`); nesse caso o diretório de dados precisa ser gravável pelo novo UID — veja o README.
RUN pip install --no-cache-dir maxminddb==2.6.2 \
 && groupadd --system --gid 10001 requestatlas \
 && useradd --system --uid 10001 --gid 10001 --no-create-home \
      --home-dir /var/lib/requestatlas --shell /usr/sbin/nologin requestatlas \
 && install -d -o 10001 -g 10001 -m 0750 /var/lib/requestatlas \
 && install -d -m 0755 /etc/requestatlas /npm /geoip

WORKDIR /app
COPY LICENSE ./
COPY requestatlas ./requestatlas
RUN python -m compileall -q requestatlas

# Layout do container (os caminhos do [paths] no TOML são sobrescritos por estas variáveis):
#   /npm                   diretório /data do NPM, somente leitura (logs/, database.sqlite, nginx/custom/)
#   /var/lib/requestatlas  banco próprio do RequestAtlas: requestatlas.db + ip-hmac.key (volume gravável)
#   /etc/requestatlas      requestatlas.toml opcional, arquivo de senha, GeoLite2 (somente leitura)
#   /geoip                 GeoLite2-City.mmdb / GeoLite2-ASN.mmdb opcionais (arquivo ausente = sem país/ASN)
# Dentro do container o servidor escuta em 0.0.0.0:7881; quem decide a exposição é o `ports:` do host.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    REQUESTATLAS_LISTEN=0.0.0.0 \
    REQUESTATLAS_PORT=7881 \
    REQUESTATLAS_DATA_DIR=/var/lib/requestatlas \
    REQUESTATLAS_LOG_DIR=/npm/logs \
    REQUESTATLAS_NPM_DB=/npm/database.sqlite \
    REQUESTATLAS_NGINX_CUSTOM_DIR=/npm/nginx/custom \
    REQUESTATLAS_GEOIP_CITY=/geoip/GeoLite2-City.mmdb \
    REQUESTATLAS_GEOIP_ASN=/geoip/GeoLite2-ASN.mmdb

USER 10001:10001
EXPOSE 7881

# /healthz não exige autenticação nem expõe dados; aceita loopback mesmo com allow_networks.
# A porta vem da configuração efetiva (TOML + variáveis), não de um valor fixo.
HEALTHCHECK --interval=60s --timeout=10s --start-period=2m --retries=3 \
  CMD ["python", "-c", "import urllib.request as r; from requestatlas import config; p = config.load()['server']['port']; r.build_opener(r.ProxyHandler({})).open(f'http://127.0.0.1:{p}/healthz', timeout=8)"]

# SIGINT encerra o `serve` de forma limpa (PID 1 não tem tratador padrão para SIGTERM).
STOPSIGNAL SIGINT
ENTRYPOINT ["python", "-m", "requestatlas"]
CMD ["serve"]
