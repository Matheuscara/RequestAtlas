# logsNPM

Painel de análise dos access logs do [Nginx Proxy Manager](https://nginxproxymanager.com/) — feito para responder o que o GoAccess não responde direito: **quanto do tráfego é bot**, **qual 403 é bloqueio intencional e qual é erro**, e **HTML × estáticos × API** sem misturar métricas.

> Projeto independente, não afiliado ao Nginx Proxy Manager.

## O que mostra

- **Visão geral** — requisições totais, páginas HTML, estáticos, API e outros; bots × não identificados; 403 por regra, 429, 5xx; IPs distintos (rotulado como estimativa).
- **Domínios** — todos os proxy hosts lidos do banco do NPM (somente leitura), com tendência por site.
- **Bots** — top bots por User-Agent (GPTBot, Googlebot, Bingbot, ClaudeBot…), evolução diária/horária, grupos (IA, buscadores, SEO, scanners…) e os UAs sem assinatura mais frequentes.
- **Status & erros** — 403 da regra `if ($http_user_agent ~* …) { return 403; }` separado do 403 da aplicação, 5xx por código e comparação **antes × depois** de eventos que você registra (ex.: “bloqueei o GPTBot”).
- **Páginas** — caminhos mais pedidos com filtros de domínio, período, status, bot e tipo.
- **Origem** — mapa por país, ASN, blocos de rede mascarados (/24, /48) e referrers.
- **Dados** — estado da coleta, arquivos lidos, linhas malformadas e o método de classificação.

Princípios: “não identificado como bot” **não** é chamado de humano; requisição de HTML **não** é visita, impressão de anúncio ou receita; IP completo nunca aparece.

## Como funciona

- Lê `proxy-host-N_access.log` e as rotações `.N.gz` (formatos `proxy` e `standard` do NPM) de forma **incremental**: cada arquivo é identificado pela primeira linha, então `.log → .1.gz → .2.gz` não causa releitura, e o offset é salvo na mesma transação dos agregados (queda no meio não duplica nem perde).
- O User-Agent é o campo logo após `[Sent-to …]` — nunca a URL nem o referrer (uma URL com “botucatu” não vira bot).
- Agrega por hora em UTC num SQLite próprio; dia/hora no fuso escolhido são montados na consulta. O offset de cada linha do log é respeitado.
- Só stdlib do Python 3.11+. `maxminddb` é opcional (país/ASN com GeoLite2).
- O banco e as configs do NPM são abertos **somente leitura**. O logsNPM nunca altera proxy hosts.

## Instalação

### Docker (ao lado do NPM oficial)

```yaml
services:
  logsnpm:
    image: ghcr.io/SEU_USUARIO/logsnpm:latest   # ou build: .
    restart: unless-stopped
    ports: ["7881:7881"]
    environment:
      LOGSNPM_LOG_DIR: /npm/logs
      LOGSNPM_NPM_DB: /npm/database.sqlite
      LOGSNPM_NGINX_CUSTOM_DIR: /npm/nginx/custom
      LOGSNPM_DEFAULT_TZ: America/Sao_Paulo
      LOGSNPM_AUTH_USER: admin
      LOGSNPM_AUTH_PASSWORD: troque-isto
    volumes:
      - ./npm/data:/npm:ro
      - logsnpm-data:/var/lib/logsnpm
volumes:
  logsnpm-data:
```

Veja `docker-compose.yml` para o exemplo completo (GeoIP e arquivo de configuração).

### Nativo (mesma máquina/LXC do NPM)

```sh
git clone https://github.com/SEU_USUARIO/logsNPM /opt/logsnpm
cp /opt/logsnpm/config.example.toml /etc/logsnpm/logsnpm.toml   # ajuste
LOGSNPM_CONFIG=/etc/logsnpm/logsnpm.toml python3 -m logsnpm check
cp /opt/logsnpm/deploy/logsnpm.service /etc/systemd/system/ && systemctl enable --now logsnpm
```

A primeira leitura processa todo o histórico disponível (≈ 5 milhões de linhas em ~40 s num LXC de 2 vCPU); depois cada ciclo lê só o que é novo.

## Personalização

Tudo fica em `logsnpm.toml` (comentado em [`config.example.toml`](config.example.toml)). Destaques:

| Seção | O que muda |
|---|---|
| `[ui]` | título, subtítulo, logo, gradiente da marca, idioma (`pt-BR`/`en`), fuso padrão e lista de fusos, período padrão, **quais páginas aparecem e em que ordem**, links extras na barra, intervalo de atualização, texto dos avisos |
| `[ui.colors]` | cor de cada tipo, classe de status, bot/não-bot e a paleta dos gráficos |
| `[sites.ID]` | nome exibido no lugar do domínio, esconder das listas, prefixos/hosts que contam como API |
| `[bots]` | bots próprios (`[[bots.custom]]`), desligar assinaturas embutidas, regex genérica |
| `[classify]` | extensões estáticas, prefixos de API, caminhos “outros” |
| `[[events]]` | marcos nos gráficos com comparação antes × depois (opcionalmente de um bot) |
| `[privacy]` | tamanho do prefixo exibido (/24, /48…) |
| `[server]` | porta, basic auth, redes permitidas, `X-Forwarded-For` |
| `[ingest]` | intervalo, retenção, hosts ignorados, globs dos logs |

Variáveis de ambiente: `LOGSNPM_CONFIG`, `LOGSNPM_LOG_DIR`, `LOGSNPM_NPM_DB`, `LOGSNPM_NGINX_CUSTOM_DIR`, `LOGSNPM_DATA_DIR`, `LOGSNPM_GEOIP_CITY`, `LOGSNPM_GEOIP_ASN`, `LOGSNPM_LISTEN`, `LOGSNPM_PORT`, `LOGSNPM_AUTH_USER`, `LOGSNPM_AUTH_PASSWORD`, `LOGSNPM_LANGUAGE`, `LOGSNPM_TITLE`, `LOGSNPM_DEFAULT_TZ`.

Mudou regras de classificação (`[classify]`, `[bots]`, `[privacy]`, `exclude_hosts`, `sites.*.api_*`)? Rode `logsnpm reindex` — o painel avisa quando for preciso.

**Novo idioma:** adicione `I18N["xx"]` em `logsnpm/web/i18n.js` (chave = texto em pt-BR) e use `language = "xx"`.

## Comandos

```
python -m logsnpm serve     # coleta + interface/API (padrão)
python -m logsnpm ingest    # um ciclo e sai
python -m logsnpm reindex   # apaga agregados e reprocessa tudo
python -m logsnpm check     # valida a config e mostra o que encontrou
```

## Desenvolvimento

```sh
python -m unittest discover -s tests
```

## Licença

MIT. Ativos de terceiros em `logsnpm/web/vendor/LICENSES.md` (ECharts Apache-2.0, Inter e JetBrains Mono OFL, Natural Earth domínio público). A base GeoLite2 **não** é distribuída: use a sua (licença MaxMind).
