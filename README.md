# logsNPM

[![ci](https://github.com/Matheuscara/logsNPM/actions/workflows/ci.yml/badge.svg)](https://github.com/Matheuscara/logsNPM/actions/workflows/ci.yml)
[![docker](https://github.com/Matheuscara/logsNPM/actions/workflows/docker.yml/badge.svg)](https://github.com/Matheuscara/logsNPM/pkgs/container/logsnpm)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**[English](README.en.md)** · Português

Painel de análise dos access logs do [Nginx Proxy Manager](https://nginxproxymanager.com/) — feito para responder o que o GoAccess não responde direito: **quanto do tráfego é bot**, **qual 403 é bloqueio intencional e qual é erro**, e **HTML × estáticos × API** sem misturar métricas.

> Projeto independente, não afiliado ao Nginx Proxy Manager.

![Visão geral](docs/screenshots/overview.png)

<table><tr>
<td><img src="docs/screenshots/bots.png" alt="Bots"></td>
<td><img src="docs/screenshots/status.png" alt="Status e antes × depois"></td>
</tr><tr>
<td><img src="docs/screenshots/origem.png" alt="Origem"></td>
<td><img src="docs/screenshots/dominios.png" alt="Domínios"></td>
</tr></table>

<sub>Screenshots com dados fictícios gerados por <code>scripts/gen_demo.py</code>.</sub>

## Testar em 1 minuto (sem NPM)

```sh
git clone https://github.com/Matheuscara/logsNPM && cd logsNPM
python3 scripts/gen_demo.py /tmp/logsnpm-demo
python3 -m logsnpm serve --config /tmp/logsnpm-demo/logsnpm.toml   # http://127.0.0.1:7881
```

## O que mostra

- **Visão geral** — requisições totais, páginas HTML, estáticos, API e outros; bots × não identificados; 403 por regra, 429, 5xx; IPs distintos (rotulado como estimativa).
- **Domínios** — todos os proxy hosts lidos do banco do NPM (somente leitura), com tendência por site.
- **Bots** — top bots por User-Agent (GPTBot, Googlebot, Bingbot, ClaudeBot…), evolução diária/horária, grupos (IA, buscadores, SEO, scanners…) e os UAs sem assinatura mais frequentes.
- **Status & erros** — 403 da regra `if ($http_user_agent ~* …) { return 403; }` separado do 403 da aplicação, 5xx por código e comparação **antes × depois** de eventos que você registra (ex.: “bloqueei o GPTBot”).
- **Exportar CSV** da tabela de páginas.
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

### Docker Compose (ao lado do NPM oficial)

Requer Docker Compose v2.24+. Imagens para `linux/amd64` e `linux/arm64` (Raspberry Pi).

```sh
git clone https://github.com/Matheuscara/logsNPM && cd logsNPM   # ou baixe só docker-compose.yml e .env.example
cp .env.example .env
mkdir -p config          # opcional: logsnpm.toml, arquivo de senha, GeoLite2 (assim a pasta é sua)
$EDITOR .env             # no mínimo LOGSNPM_NPM_DATA=/caminho/do/npm/data
docker compose up -d
docker compose ps        # "healthy" enquanto a coleta completa ciclos
```

Abra `http://127.0.0.1:7881` **no próprio host do Docker**: por padrão a porta só é publicada em `127.0.0.1`. De outra máquina use um túnel (`ssh -L 7881:127.0.0.1:7881 usuario@host`), [exponha com autenticação](#acesso-e-autenticação) ou [publique pelo NPM](#publicar-pelo-npm-proxy-reverso).

Todo o ajuste fica no `.env` ([`.env.example`](.env.example) é comentado linha a linha): lado do host (imagem, IP/porta publicados, UID:GID, diretórios) e, no mesmo arquivo, as variáveis do app, repassadas ao container.

#### O que é montado

| Variável no `.env` | No container | Modo | Conteúdo |
|---|---|---|---|
| `LOGSNPM_NPM_DATA` + `/logs` | `/npm/logs` | leitura | access logs do NPM (obrigatório) |
| `LOGSNPM_NPM_DATA` + `/database.sqlite` (ou `LOGSNPM_NPM_DB_FILE`) | `/npm/database.sqlite` | leitura | proxy hosts, domínios e regras de 403 do NPM |
| `LOGSNPM_NPM_DATA` + `/nginx` | `/npm/nginx` | leitura | `custom/` com o bloco `geo $blocked_ip` (opcional) |
| `LOGSNPM_DATA_VOLUME` (padrão: volume `logsnpm-data`) | `/var/lib/logsnpm` | escrita | `logsnpm.db`, os agregados do logsNPM |
| `LOGSNPM_CONFIG_DIR` (padrão: `./config`) | `/etc/logsnpm` | leitura | `logsnpm.toml`, `password`, GeoLite2 — tudo opcional |
| `LOGSNPM_GEOIP_DIR` (padrão: o diretório de config) | `/geoip` | leitura | `GeoLite2-City.mmdb`, `GeoLite2-ASN.mmdb` (opcionais) |

- **Só o necessário do NPM entra, somente leitura.** `keys.json`, `custom_ssl/` (chaves privadas) e `access/` ficam fora. O `database.sqlite` em si guarda dados sensíveis (hashes dos usuários do NPM e, com desafio DNS, credenciais do provedor); o logsNPM só consulta as tabelas de hosts, mas o arquivo fica visível no container — por isso ele roda sem root, com sistema de arquivos somente leitura, sem capabilities e publicado só em `127.0.0.1` por padrão.
- Os caminhos do NPM precisam existir: nada é criado no host, e um caminho errado falha com `bind source path does not exist`.
- **NPM com MySQL/MariaDB/Postgres** não tem `database.sqlite`: use `LOGSNPM_NPM_DB_FILE=/dev/null`. Os logs são lidos e classificados igual (bots, status, páginas, origem), mas domínios e regras de 403 por User-Agent só aparecem se você declarar `domains`/`ua_rules` em `[sites.ID]` no `logsnpm.toml`; destino, SSL e estado ativo/removido dos hosts ficam indisponíveis.
- O diretório de config é sempre montado, nunca um arquivo solto: sem `logsnpm.toml` dentro, valem os padrões. Se a pasta não existir, o Docker cria uma vazia (com dono root) — daí o `mkdir -p config`.
- Os caminhos `[paths]` do TOML não valem no container: a imagem define `LOGSNPM_LOG_DIR=/npm/logs`, `LOGSNPM_NPM_DB=/npm/database.sqlite`, `LOGSNPM_NGINX_CUSTOM_DIR=/npm/nginx/custom`, `LOGSNPM_DATA_DIR=/var/lib/logsnpm` e `LOGSNPM_GEOIP_CITY`/`_ASN=/geoip/GeoLite2-*.mmdb` (arquivo ausente = sem país/ASN, sem erro). Dentro do container o servidor escuta sempre em `0.0.0.0:7881`; mude a porta do host com `LOGSNPM_HOST_PORT`, nunca `server.port`.

A primeira coleta processa todo o histórico disponível (≈ 5 milhões de linhas em ~40 s num LXC de 2 vCPU) e o container continua `healthy` durante ela; depois cada ciclo lê só o que é novo. O `HEALTHCHECK` consulta `/healthz`, que fica `unhealthy` se a coleta parar de completar ciclos.

#### Acesso e autenticação

O padrão é privado: `LOGSNPM_BIND=127.0.0.1`. Para abrir na rede local, troque o IP **e** ative basic auth:

```sh
# .env
LOGSNPM_BIND=192.168.1.10                         # IP do host na LAN (0.0.0.0 = todas as interfaces)
LOGSNPM_AUTH_USER=admin
LOGSNPM_AUTH_PASSWORD_FILE=/etc/logsnpm/password
```

```sh
openssl rand -base64 24 > config/password && cat config/password    # anote a senha
sudo chown 10001:10001 config/password && sudo chmod 0400 config/password   # LOGSNPM_UID:LOGSNPM_GID
docker compose up -d
```

- Alternativa: `LOGSNPM_AUTH_PASSWORD='...'` direto no `.env`, ou `auth_user`/`auth_password` no TOML. Não combine senha em arquivo com outra senha: a inicialização falha.
- Basic auth trafega em texto claro sobre HTTP; fora de uma LAN confiável, publique pelo NPM com HTTPS.
- `server.allow_networks` restringe por IP de origem. No Docker, conexões ao `127.0.0.1` do host passam pelo docker-proxy e chegam com o IP do gateway da rede Docker (ex.: `172.18.0.1`); inclua essa rede se usar a lista.
- `/healthz` é o único endpoint sem autenticação: responde só `ok`/`stale`, aceito do loopback ou de `allow_networks`.

#### Publicar pelo NPM (proxy reverso)

1. Coloque o logsNPM na rede Docker do NPM: descomente o bloco `networks` no `docker-compose.yml` com o nome da rede (`docker network ls`, ex.: `npm_default`).
2. No NPM, crie um Proxy Host para `http://logsnpm:7881`, com SSL e uma Access List. A porta do host pode continuar em `127.0.0.1`.
3. Para que `allow_networks` enxergue o IP real do visitante, aceite `X-Forwarded-For` **somente** vindo do NPM:
   ```toml
   [server]
   trust_x_forwarded_for = true
   trusted_proxy_networks = ["172.18.0.0/16"]   # docker network inspect npm_default -f '{{(index .IPAM.Config 0).Subnet}}'
   ```
   `trust_x_forwarded_for` sem `trusted_proxy_networks` é erro de configuração. O cabeçalho é lido da direita para a esquerda, pulando os proxies confiáveis, então um `X-Forwarded-For` forjado pelo cliente não vale; vindo de qualquer outro IP, é ignorado.
4. Ponha o domínio do painel em `ingest.exclude_hosts` para ele não entrar nas próprias estatísticas.

#### Permissões

- O processo roda como `10001:10001` (`LOGSNPM_UID`/`LOGSNPM_GID`), com raiz somente leitura, `/tmp` em tmpfs, `cap_drop: ALL` e `no-new-privileges`.
- **Leitura do NPM:** os logs e o `database.sqlite` do NPM costumam ser legíveis por todos (`0644`). Se não forem, dê leitura ao UID com ACL (ex.: `sudo setfacl -R -m u:10001:rX /caminho/npm/data/logs && sudo setfacl -d -m u:10001:rX /caminho/npm/data/logs`, a segunda para os arquivos novos da rotação) ou use um GID que já leia esses arquivos.
- **Escrita dos dados:** o volume `logsnpm-data` nasce com dono `10001:10001` (copiado da imagem). Trocou UID/GID? O novo usuário não consegue gravar até você ajustar: use um diretório do host com o dono certo (`mkdir -p data && sudo chown 1000:1000 data` e `LOGSNPM_DATA_VOLUME=./data`) ou corrija o volume existente uma vez (`docker run --rm -v <projeto>_logsnpm-data:/d alpine chown -R 1000:1000 /d`; o nome aparece em `docker volume ls`).
- `logsnpm.toml` e `password` no diretório de config precisam ser legíveis pelo UID.

#### Imagem publicada ou build local

- Padrão: `ghcr.io/matheuscara/logsnpm:latest`. Para fixar versão, `LOGSNPM_IMAGE=ghcr.io/matheuscara/logsnpm:<tag>` (tags `X.Y.Z`/`X.Y` das releases e `sha-<commit>`). Atualizar: `docker compose pull && docker compose up -d`.
- Do código-fonte: `LOGSNPM_IMAGE=logsnpm:local` e `docker compose up -d --build`. O contexto de build só leva `logsnpm/` e `LICENSE` (`.dockerignore`), então `.env`, `logsnpm.toml`, `config/` e dados nunca entram na imagem.
- O volume de dados continua `logsnpm-data`: quem vem do compose antigo mantém os agregados.

#### Comandos no Docker

```sh
docker compose exec logsnpm python -m logsnpm check   # configuração efetiva e o que foi encontrado
docker compose logs -f logsnpm

# reindex (após mudar regras de classificação): pare o serviço para não haver duas ingestões
docker compose stop logsnpm
docker compose run --rm logsnpm reindex
docker compose start logsnpm
```

### Nativo (mesma máquina/LXC do NPM)

```sh
git clone https://github.com/Matheuscara/logsNPM /opt/logsnpm
mkdir -p /etc/logsnpm && cp /opt/logsnpm/config.example.toml /etc/logsnpm/logsnpm.toml   # ajuste
cd /opt/logsnpm && LOGSNPM_CONFIG=/etc/logsnpm/logsnpm.toml python3 -m logsnpm check
cp /opt/logsnpm/deploy/logsnpm.service /etc/systemd/system/ && systemctl enable --now logsnpm
```

Fora do Docker o servidor escuta em `127.0.0.1:7881` por padrão. Para a rede, defina `server.listen` **e** basic auth (ou publique por um proxy reverso, como o exemplo em [`deploy/nginx-npm-custom-http.conf`](deploy/nginx-npm-custom-http.conf)).

## Personalização

Tudo pode ficar em `logsnpm.toml` (comentado em [`config.example.toml`](config.example.toml)) ou em variáveis de ambiente. Destaques:

| Seção | O que muda |
|---|---|
| `[ui]` | título, subtítulo, logo, gradiente da marca, idioma (`pt-BR`/`en`), fuso padrão e lista de fusos, período padrão, **quais páginas aparecem e em que ordem**, links extras na barra, intervalo de atualização, texto dos avisos |
| `[ui.colors]` | cor de cada tipo, classe de status, bot/não-bot e a paleta dos gráficos |
| `[sites.ID]` | nome exibido no lugar do domínio, esconder das listas, prefixos/hosts que contam como API, domínios e regras de 403 por User-Agent (substituem as do banco do NPM; essenciais com MySQL/MariaDB/Postgres) |
| `[bots]` | bots próprios (`[[bots.custom]]`), desligar assinaturas embutidas, regex genérica |
| `[classify]` | extensões estáticas, prefixos de API, caminhos “outros” |
| `[[events]]` | marcos nos gráficos com comparação antes × depois (opcionalmente de um bot) |
| `[privacy]` | tamanho do prefixo exibido (/24, /48…) |
| `[server]` | endereço/porta, basic auth, redes permitidas, `X-Forwarded-For` e proxies confiáveis |
| `[ingest]` | intervalo, retenção, hosts ignorados, globs dos logs |

**Aparência sem editar arquivo:** o botão “Aparência” (ícone de paleta, na barra do topo) faz uma prévia de título, subtítulo e cores só no seu navegador. “Baixar TOML” gera um `logsnpm-ui.toml` com `[ui]`/`[ui.colors]` para mesclar no `logsnpm.toml`; a mudança vale para todos depois de reiniciar. “Restaurar padrões do servidor” descarta a prévia.

### Arquivo, variáveis e precedência

O arquivo é o de `--config` ou `$LOGSNPM_CONFIG` (precisa existir); sem eles, o primeiro que existir entre `./logsnpm.toml` e `/etc/logsnpm/logsnpm.toml` — no Docker, `$LOGSNPM_CONFIG_DIR/logsnpm.toml`. Por cima dele:

**padrões < `logsnpm.toml` < atalhos `LOGSNPM_*` < `LOGSNPM__SEÇÃO__CHAVE`**

e, por último, `LOGSNPM_AUTH_PASSWORD_FILE` preenche `server.auth_password` (erro se já houver senha). Opção desconhecida ou de tipo errado impede a inicialização com a mensagem do problema — rode `logsnpm check`.

Qualquer opção do TOML vira variável com **dois** sublinhados:

| TOML | Variável |
|---|---|
| `[ui]` `title` | `LOGSNPM__UI__TITLE` |
| `[ui]` `refresh_seconds` | `LOGSNPM__UI__REFRESH_SECONDS` |
| `[ui.colors]` (tabela inteira) | `LOGSNPM__UI__COLORS` |
| `[server]` `allow_networks` | `LOGSNPM__SERVER__ALLOW_NETWORKS` |
| `[sites.30]` `name` | `LOGSNPM__SITES__30__NAME` |
| `[[events]]` (lista inteira) | `LOGSNPM__EVENTS__ITEMS` |

O valor é lido como JSON — número, `true`/`false`, lista, objeto ou `"texto"` — e, se não for JSON válido, vira texto puro:

```sh
LOGSNPM__UI__TITLE=Painel do proxy                 # texto
LOGSNPM__UI__TITLE='"2026"'                        # texto que pareceria número: aspas JSON
LOGSNPM__UI__REFRESH_SECONDS=30                    # número
LOGSNPM__UI__SHOW_CAVEATS=false                    # booleano
LOGSNPM__UI__ACCENT='["#f97316", "#facc15"]'       # lista
LOGSNPM__UI__COLORS='{"html": "#f97316", "bot": "#a78bfa"}'   # substitui todo o [ui.colors]
LOGSNPM__EVENTS__ITEMS='[{"ts": "2026-10-05T10:06:16-03:00", "title": "GPTBot bloqueado", "bot": "GPTBot"}]'
```

Tabelas e listas passadas por variável substituem as do arquivo inteiras; `LOGSNPM__SITES__<ID>__<CHAVE>` muda só aquela chave do site. No `.env` do Compose, use aspas simples em JSON e em valores com `$` ou `#`. Para muitos eventos ou bots próprios, o `logsnpm.toml` continua mais legível.

Atalhos `LOGSNPM_*` (aplicados mesmo vazios): `LOGSNPM_CONFIG`, `LOGSNPM_LOG_DIR`, `LOGSNPM_NPM_DB`, `LOGSNPM_NGINX_CUSTOM_DIR`, `LOGSNPM_DATA_DIR`, `LOGSNPM_GEOIP_CITY`, `LOGSNPM_GEOIP_ASN`, `LOGSNPM_LISTEN`, `LOGSNPM_PORT`, `LOGSNPM_AUTH_USER`, `LOGSNPM_AUTH_PASSWORD`, `LOGSNPM_AUTH_PASSWORD_FILE`, `LOGSNPM_LANGUAGE`, `LOGSNPM_TITLE`, `LOGSNPM_DEFAULT_TZ`.

### Reindex

Mudou regras de classificação (`[classify]`, `[bots]`, `[privacy]`, `ingest.exclude_hosts`, `sites.*.api_*`, `sites.*.ua_rules`)? Os dados antigos foram agregados com as regras anteriores: pare o serviço, rode `logsnpm reindex` com a mesma configuração e inicie de novo (no Docker, os três comandos acima). O painel avisa quando for preciso. Título, cores e o resto de `[ui]` só pedem reinício.

**Novo idioma:** adicione `I18N["xx"]` em `logsnpm/web/i18n.js` (chave = texto em pt-BR) e use `language = "xx"`.

## Comandos

```
python -m logsnpm serve     # coleta + interface/API (padrão)
python -m logsnpm ingest    # um ciclo e sai
python -m logsnpm reindex   # apaga agregados e reprocessa tudo (com o serviço parado)
python -m logsnpm check     # valida a config e mostra o que encontrou
```

## Desenvolvimento

```sh
python -m unittest discover -s tests
```

## Licença

MIT. Ativos de terceiros em `logsnpm/web/vendor/LICENSES.md` (ECharts Apache-2.0, Inter e JetBrains Mono OFL, Natural Earth domínio público). A base GeoLite2 **não** é distribuída: use a sua (licença MaxMind).
