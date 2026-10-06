# logsNPM

[![ci](https://github.com/Matheuscara/logsNPM/actions/workflows/ci.yml/badge.svg)](https://github.com/Matheuscara/logsNPM/actions/workflows/ci.yml) [![docker](https://github.com/Matheuscara/logsNPM/actions/workflows/docker.yml/badge.svg)](https://github.com/Matheuscara/logsNPM/pkgs/container/logsnpm) [![MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**[English](README.en.md)** · Português

**Entenda o tráfego que chega ao seu Nginx Proxy Manager.** Veja o que é HTML, arquivo estático ou API; quais bots aparecem no User-Agent; e se um `403` é um bloqueio esperado ou um erro da aplicação. Sem mexer nos proxy hosts do NPM.


## Comece pelo seu caso

### Tenho o NPM rodando em Docker

**Pré-requisito:** Docker Compose v2.24+ e acesso ao diretório que o NPM monta como `/data`.

1. Baixe o projeto e prepare o arquivo de configuração:

   ```sh
   git clone https://github.com/Matheuscara/logsNPM.git
   cd logsNPM
   cp .env.example .env
   mkdir -p config
   ```

2. Abra `.env` e preencha **`LOGSNPM_NPM_DATA`** com o caminho *absoluto* da pasta `data` do seu NPM. Se o Compose do NPM diz `./data:/data`, é o `./data` daquele projeto, convertido em caminho absoluto. Por exemplo: `LOGSNPM_NPM_DATA=/srv/nginx-proxy-manager/data`.

3. Suba e confira:

   ```sh
   docker compose up -d
   docker compose ps
   ```

4. Abra **<http://127.0.0.1:7881> no próprio host Docker**. De outro computador, faça um túnel (`ssh -L 7881:127.0.0.1:7881 usuario@host`) ou [publique pelo NPM com HTTPS e controle de acesso](#publicar-pelo-próprio-npm).

A primeira leitura processa os logs existentes. Depois, só lê as linhas novas. País e ASN exigem bases GeoLite2 opcionais; o restante funciona sem elas.

### Quero testar sem ter NPM

```sh
git clone https://github.com/Matheuscara/logsNPM.git
cd logsNPM
python3 scripts/gen_demo.py /tmp/logsnpm-demo
python3 -m logsnpm serve --config /tmp/logsnpm-demo/logsnpm.toml
```

Abra <http://127.0.0.1:7881>. O demo cria **logs, domínios e um bloqueio fictícios**; não usa seus dados.

![Visão geral do logsNPM, com dados fictícios](docs/screenshots/overview.png)

> Projeto independente, não afiliado ao Nginx Proxy Manager. As imagens usam [dados fictícios](scripts/gen_demo.py).

<details>
<summary>Ver outras telas</summary>

| Bots | Status e bloqueios |
|---|---|
| ![Bots](docs/screenshots/bots.png) | ![Status](docs/screenshots/status.png) |

| Origem | Domínios |
|---|---|
| ![Origem](docs/screenshots/origem.png) | ![Domínios](docs/screenshots/dominios.png) |

</details>

## O que você consegue ver

- **Visão geral:** total de requisições, HTML, estáticos, API, bots identificados, `403` por regra, `429` e `5xx` — cada coisa no seu lugar.
- **Domínios e páginas:** tráfego por proxy host; caminhos mais acessados com filtros por período, status, bot e tipo. A tabela de páginas exporta CSV.
- **Bots e origem:** top bots e evolução por hora/dia; país, ASN, redes mascaradas e referrers.
- **Status e dados:** falhas reais separadas dos bloqueios intencionais, comparação antes/depois de eventos configurados e estado da leitura dos logs.

**Limite importante:** uma requisição HTML **não** é visitante humano, anúncio exibido, cadastro nem receita. “Não identificado como bot” também **não** significa humano. IPs distintos são uma **estimativa**, não pessoas; IPs completos não aparecem no painel.

## Personalize do seu jeito

**Só quero experimentar cores:** clique em **Aparência** (ícone de paleta no topo). Título e cores mudam imediatamente **só neste navegador**. Para aplicar a todos, use **Baixar TOML**, mescle as chaves no seu `config/logsnpm.toml` e reinicie o container. O painel não escreve na configuração do servidor.

**Quero deixar permanente:** crie `config/logsnpm.toml` a partir de [`config.example.toml`](config.example.toml). Exemplo:

```toml
[ui]
title = "Meu painel"
language = "pt-BR"
default_tz = "America/Sao_Paulo"
accent = ["#f97316", "#ec4899"]
pages = ["overview", "sites", "bots", "status", "pages"]

[sites.1]
name = "Minha loja"
api_prefixes = ["/api/"]
```

O arquivo pode ajustar marca, idioma, ordem das páginas, cores, bots próprios, classificação de URLs, domínios, eventos e privacidade. A opção [`config.example.toml`](config.example.toml) mostra **todas** as chaves; [`.env.example`](.env.example) mostra os controles do Docker. Configurações simples também podem ser passadas por variáveis de ambiente, como `LOGSNPM__UI__TITLE=Meu painel`.

> Mudou a forma de classificar requisições ou bots? [Reprocesse os logs](#reprocessar-depois-de-mudar-as-regras). Mudanças só de aparência exigem apenas reiniciar.

## Antes de expor na rede

Por padrão, o Docker publica o painel **apenas em `127.0.0.1`**. Se mudar `LOGSNPM_BIND` para um IP da LAN ou `0.0.0.0`, configure autenticação e, fora de uma LAN confiável, HTTPS. Os logs podem revelar caminhos e hábitos de acesso.

O logsNPM roda sem root e monta logs e banco do NPM **somente para leitura**. Mesmo assim, `database.sqlite` pode conter dados sensíveis: proteja o container e não exponha o painel sem controle de acesso. O banco **próprio** do logsNPM fica num volume separado.

<details>
<summary>Configurar usuário e senha</summary>

No `.env`:

```dotenv
LOGSNPM_AUTH_USER=admin
LOGSNPM_AUTH_PASSWORD_FILE=/etc/logsnpm/password
# Se abrir na LAN: LOGSNPM_BIND=192.168.1.10
```

Crie a senha no host e deixe-a legível pelo UID do container (padrão `10001`):

```sh
openssl rand -base64 24 > config/password
sudo chown 10001:10001 config/password
sudo chmod 0400 config/password
docker compose up -d
```

Basic Auth sobre HTTP não criptografa a senha. Use HTTPS se houver acesso fora de uma rede confiável. `/healthz` é o único endpoint sem login; responde somente `ok` ou `stale`.

</details>

<details>
<summary>Publicar pelo próprio NPM</summary>

1. No [`docker-compose.yml`](docker-compose.yml), descomente o bloco `networks` e indique a rede Docker do NPM (`docker network ls`).
2. Crie no NPM um Proxy Host apontando para `http://logsnpm:7881`, com **SSL e Access List**. A porta no host pode continuar privada em `127.0.0.1`.
3. Se usar `server.allow_networks` para filtrar pelo IP real do visitante, configure `trust_x_forwarded_for = true` **e** `trusted_proxy_networks = ["CIDR_DA_REDE_DOCKER_DO_NPM"]` em `[server]`. Sem a lista de proxies confiáveis, a configuração é rejeitada. O cabeçalho é lido da direita para a esquerda para evitar um IP falso enviado pelo cliente.
4. Inclua o domínio do painel em `ingest.exclude_hosts` para não contar seus próprios acessos.

</details>

## Ajuda para instalar e operar

<details>
<summary>Onde ficam os arquivos no Docker?</summary>

| Origem | Uso | Acesso do logsNPM |
|---|---|---|
| `LOGSNPM_NPM_DATA/logs` | Logs ativos e `.gz` do NPM | Somente leitura |
| `LOGSNPM_NPM_DATA/database.sqlite` | Nomes dos proxy hosts e regras do Advanced | Somente leitura |
| `LOGSNPM_NPM_DATA/nginx` | Blocos `geo` personalizados, se houver | Somente leitura |
| `LOGSNPM_DATA_VOLUME` | Agregados do logsNPM | Escrita |
| `LOGSNPM_CONFIG_DIR` (padrão `./config`) | `logsnpm.toml` e arquivo de senha | Somente leitura |

O Compose monta **só** esses caminhos do NPM; não monta `keys.json`, `custom_ssl/` ou `access/`. Os caminhos de logs, banco e nginx precisam existir, ou o Docker falha sem criar pastas silenciosamente. `LOGSNPM_HOST_PORT` troca a porta no **host**; dentro do container ela permanece `7881`.

`LOGSNPM_IMAGE` escolhe a imagem (padrão `ghcr.io/matheuscara/logsnpm:latest`). Para compilar do código-fonte, use `LOGSNPM_IMAGE=logsnpm:local` e `docker compose up -d --build`. Há imagens para amd64 e arm64.

</details>

<details>
<summary>Meu NPM usa MySQL/MariaDB, não database.sqlite</summary>

No `.env`, defina `LOGSNPM_NPM_DB_FILE=/dev/null`. Os **logs continuam funcionando**. Como não há banco SQLite para ler, informe no `config/logsnpm.toml` os domínios e as regras que deseja atribuir:

```toml
[sites.1]
name = "Minha loja"
domains = ["loja.exemplo.com", "www.loja.exemplo.com"]
ua_rules = [{ pattern = "GPTBot", status = 403 }]
```

O ID `1` vem do nome `proxy-host-1_access.log`. Sem o banco do NPM, destino do proxy, SSL e estado ativado/removido não podem ser exibidos. Se declarar `ua_rules` para um site com SQLite, essa lista **substitui** as regras detectadas no Advanced para esse site.

</details>

<details>
<summary>Permissões: container não consegue ler logs ou gravar agregados</summary>

O processo roda como `10001:10001` por padrão; `LOGSNPM_UID` e `LOGSNPM_GID` no `.env` podem mudar isso. Ele precisa ler os logs e o `database.sqlite` do NPM e gravar em `LOGSNPM_DATA_VOLUME`.

- Logs sem permissão: dê acesso de leitura ao UID/GID escolhido; para rotações futuras, configure também a ACL padrão no diretório de logs.
- UID/GID diferente de `10001`: prepare um diretório gravável no host (ex.: `mkdir -p data && sudo chown 1000:1000 data`, com `LOGSNPM_DATA_VOLUME=./data`) ou corrija o dono do volume Docker existente.
- `config/logsnpm.toml` e `config/password` precisam ser legíveis pelo mesmo UID.

Confira `docker compose logs logsnpm` e `docker compose exec logsnpm python -m logsnpm check`. O `.env.example` documenta os caminhos, UID e GID.

</details>

<details>
<summary>Instalação nativa, sem Docker</summary>

Requer Python 3.11+ na mesma máquina/LXC que acessa os logs do NPM:

```sh
git clone https://github.com/Matheuscara/logsNPM /opt/logsnpm
mkdir -p /etc/logsnpm
cp /opt/logsnpm/config.example.toml /etc/logsnpm/logsnpm.toml
# Ajuste os caminhos no TOML antes de continuar.
cd /opt/logsnpm
LOGSNPM_CONFIG=/etc/logsnpm/logsnpm.toml python3 -m logsnpm check
cp deploy/logsnpm.service /etc/systemd/system/
systemctl enable --now logsnpm
```

Fora do Docker, `server.listen` é `127.0.0.1` por padrão. Para expor, configure autenticação e um proxy reverso com HTTPS. Há um exemplo em [`deploy/nginx-npm-custom-http.conf`](deploy/nginx-npm-custom-http.conf).

</details>

<details>
<summary>Variáveis de ambiente e precedência</summary>

A ordem é: **padrões → `logsnpm.toml` → atalhos `LOGSNPM_*` → `LOGSNPM__SEÇÃO__CHAVE`**. `LOGSNPM_AUTH_PASSWORD_FILE` preenche a senha por último e não pode coexistir com outra senha. Opção desconhecida ou de tipo errado impede a inicialização.

Exemplos no `.env`:

```dotenv
LOGSNPM__UI__TITLE=Painel do proxy
LOGSNPM__UI__SHOW_CAVEATS=false
LOGSNPM__UI__ACCENT='["#f97316", "#facc15"]'
LOGSNPM__UI__PAGES='["overview", "bots", "pages"]'
LOGSNPM__SITES__1__NAME=Minha loja
LOGSNPM__EVENTS__ITEMS='[{"ts":"2026-10-05T10:06:16-03:00","title":"Bloqueio do GPTBot","site":1}]'
```

O valor é JSON para números, booleanos, listas e tabelas; texto simples também é aceito. Uma lista/tabela passada por ambiente **substitui** a do TOML. O mesmo `.env` controla `LOGSNPM_BIND`, `LOGSNPM_HOST_PORT`, `LOGSNPM_UID` e `LOGSNPM_GID` no lado do Compose. A lista completa está em [`.env.example`](.env.example).

</details>

### Reprocessar depois de mudar as regras

Se mudar `[bots]`, `[classify]`, `[privacy]`, `ingest.exclude_hosts` ou `sites.*.api_*/ua_rules`, os agregados antigos mantêm a classificação anterior. Para aplicar a todo o histórico:

```sh
docker compose stop logsnpm
docker compose run --rm logsnpm reindex
docker compose start logsnpm
```

O painel avisa quando um reindex é necessário. **Não execute duas ingestões ao mesmo tempo.** Mudanças de título e cores não exigem reindex.

<details>
<summary>Como os números são produzidos?</summary>

O programa lê `proxy-host-N_access.log` e rotações `.N.gz` incrementalmente. Identifica o arquivo pela primeira linha; salva o offset junto com os agregados no próprio SQLite, evitando releitura e contagem duplicada após queda ou rotação. O User-Agent vem do campo logo após `[Sent-to …]`, nunca da URL ou do referrer. Os totais são agregados por hora em UTC e apresentados no fuso escolhido. O banco e as configurações do NPM são abertos em modo somente leitura.

`python -m logsnpm serve` executa painel e coletor; `ingest` lê um ciclo; `check` valida caminhos/configuração; `reindex` refaz os agregados. Para desenvolver, rode `python -m unittest discover -s tests`.

</details>

## Licença

MIT. Licenças dos recursos incluídos em [`logsnpm/web/vendor/LICENSES.md`](logsnpm/web/vendor/LICENSES.md). A base GeoLite2 **não** é distribuída; use uma sua conforme a licença da MaxMind.
