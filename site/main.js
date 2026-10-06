// RequestAtlas landing: mobile menu, copy buttons, EN/PT toggle, restrained reveal.
(() => {
  const root = document.documentElement;

  // --- mobile menu
  const menuBtn = document.querySelector(".menu-btn");
  const menu = document.getElementById("menu");
  const setMenu = (open) => {
    menu.classList.toggle("open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
  };
  menuBtn.addEventListener("click", () => setMenu(!menu.classList.contains("open")));
  menu.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menu.classList.contains("open")) { setMenu(false); menuBtn.focus(); }
  });

  // --- copy buttons
  for (const btn of document.querySelectorAll(".copy")) {
    btn.addEventListener("click", async () => {
      const code = btn.parentElement.querySelector("pre").innerText.trim();
      try {
        await navigator.clipboard.writeText(code);
        btn.dataset.state = "done";
      } catch {
        btn.dataset.state = "fail";
      }
      btn.classList.toggle("done", btn.dataset.state === "done");
      btn.textContent = T(btn.dataset.state === "done" ? "copied" : "copyFail");
      setTimeout(() => { btn.classList.remove("done"); btn.textContent = T("copy"); }, 1800);
    });
  }

  // --- language
  const PT = {
    "skip": "Pular para o conteúdo",
    "menu": "Menu",
    "copy": "Copiar", "copied": "Copiado", "copyFail": "Selecione e copie",
    "nav.features": "Recursos", "nav.quickstart": "Instalação", "nav.customize": "Personalizar", "nav.privacy": "Privacidade",
    "hero.eyebrow": "Auto-hospedado · código aberto · para Nginx Proxy Manager",
    "hero.title": "Veja o que <em>realmente</em> chega no seu proxy reverso.",
    "hero.lead": "O RequestAtlas lê os logs de acesso do Nginx Proxy Manager e monta um painel privado: páginas vs. arquivos estáticos vs. chamadas de API, bots identificados pelo User-Agent, e os 403 que você quis enviar separados das falhas 5xx reais.",
    "hero.cta": "Instale em 3 passos",
    "hero.github": "Ver o código no GitHub",
    "hero.f1": "Licença MIT",
    "hero.f2": "Imagem Docker amd64 e arm64",
    "hero.f3": "Sem mexer nos seus proxy hosts",
    "hero.alt": "Visão geral do RequestAtlas: total de requisições dividido em HTML, estáticos e API; bots por User-Agent; 403 por regra separado de 5xx; gráfico por hora com o evento 'Blocked GPTBot'; principais domínios em example.com. Todos os números são fictícios.",
    "hero.caption": "Ilustração com dados de exemplo fictícios.",
    "features.eyebrow": "O que ele mostra",
    "features.title": "Cada requisição, organizada em algo que dá para usar",
    "features.lead": "O log de acesso já sabe quem pediu o quê. O RequestAtlas lê os logs de forma incremental — inclusive os <code>.gz</code> rotacionados — e responde o que você procuraria com grep.",
    "f1.t": "Páginas, arquivos e API — separados",
    "f1.d": "Cada requisição é classificada como HTML, arquivo estático ou chamada de API, então uma rajada de downloads de JavaScript não parece pico de tráfego. Prefixos e extensões são configuráveis por site.",
    "f2.t": "Bots pelo User-Agent",
    "f2.d": "Assinaturas embutidas para buscadores, crawlers de IA e ferramentas de SEO, além dos seus próprios padrões. Veja os bots declarados que mais acessam e a tendência por hora ou dia.",
    "f3.t": "403 esperado ≠ falha real",
    "f3.d": "Bloqueios que você configurou no NPM — regras de User-Agent ou listas <code>geo</code> de IP — contam como 403 intencional, separados de limites 429 e erros 5xx do upstream.",
    "f4.t": "Páginas por domínio",
    "f4.d": "Tráfego de cada proxy host e seus caminhos mais acessados, filtrados por período, status, bot e tipo — com exportação CSV.",
    "f5.t": "Origem, com privacidade",
    "f5.d": "País e ASN a partir dos seus próprios arquivos GeoLite2 (opcionais). IPs completos nunca aparecem — só redes /24 ou /48 — e contagens de IPs distintos vêm marcadas como estimativa.",
    "f6.t": "Eventos e antes/depois",
    "f6.d": "Marque uma mudança como “Bloqueei o GPTBot” e compare os períodos em volta dela. A página de ingestão mostra se os números estão atualizados.",
    "honest.t": "Números que não exageram",
    "honest.d": "Uma requisição HTML não é um visitante, uma impressão de anúncio nem uma venda. “Não identificado como bot” não quer dizer humano. IPs distintos não são pessoas. O RequestAtlas diz isso no próprio painel em vez de esconder.",
    "qs.eyebrow": "Instalação",
    "qs.title": "Rodando em três passos",
    "qs.lead": "Você precisa do Docker Compose v2.24+ e do diretório do host que o Nginx Proxy Manager monta em <code>/data</code>.",
    "qs.s1": "Baixe os arquivos",
    "qs.s1d": "Clone o repositório e crie o <code>.env</code> e o <code>config/</code> que você vai editar.",
    "qs.s2": "Aponte para o NPM",
    "qs.s2d": "No <code>.env</code>, informe o caminho <em>absoluto</em> do diretório de dados do NPM:",
    "qs.s3": "Suba o container",
    "qs.s3d": "Abra <code>http://127.0.0.1:7881</code> no host do Docker. Ele escuta só em loopback; acesse por túnel SSH ou publique pelo NPM com HTTPS e Access List.",
    "demo.t": "Sem NPM por perto? Teste o demo",
    "demo.d": "Com Python 3.11+, gere logs, domínios e um evento de bloqueio de bot fictícios e abra o mesmo endereço.",
    "more.t": "Guia completo",
    "more.d": "Permissões, autenticação, NPM com MySQL/MariaDB, instalação nativa com systemd e reprocessamento após mudar regras estão no README.",
    "cz.eyebrow": "Personalização",
    "cz.title": "Seu painel, suas regras",
    "cz.l1": "<strong>Prévia no navegador.</strong> O painel Aparência testa título e cores localmente e baixa um trecho TOML. O painel nunca grava a configuração do servidor.",
    "cz.l2": "<strong>Configure em um único TOML.</strong> Marca, interface em português ou inglês, fuso horário, ordem das páginas, cores, bots próprios, classificação de URLs, domínios, eventos e privacidade.",
    "cz.l3": "<strong>Sobrescreva pelo ambiente.</strong> Qualquer chave funciona como <code>REQUESTATLAS__SECAO__CHAVE</code>, então quem usa Compose pode ficar só no <code>.env</code>.",
    "cz.l4": "<strong>Modo só logs.</strong> NPM em MySQL ou MariaDB? A análise dos logs continua funcionando — nomeie domínios e regras de bloqueio no TOML.",
    "pv.eyebrow": "Privacidade e segurança",
    "pv.title": "Feito para ficar no seu servidor",
    "pv1.t": "Acesso somente leitura ao NPM",
    "pv1.d": "Só os logs, o <code>database.sqlite</code> e a config do nginx são montados, somente leitura. <code>keys.json</code>, certificados próprios e access lists do NPM ficam de fora. Os agregados ficam no volume do próprio RequestAtlas.",
    "pv2.t": "Privado por padrão",
    "pv2.d": "Publicado só em <code>127.0.0.1</code> e rodando sem root. Basic Auth opcional a partir de um arquivo de senha; <code>/healthz</code> é o único endpoint aberto.",
    "pv3.t": "Sem IP bruto no banco dele",
    "pv3.d": "Endereços são guardados como hash com chave, com um segredo gerado no seu servidor, ao lado da rede mascarada. A interface mostra redes, nunca IPs completos.",
    "faq.title": "Perguntas",
    "q1": "Funciona com Nginx puro, Caddy ou Traefik?",
    "a1": "Hoje não. O parser lê o formato de log dos proxy hosts do Nginx Proxy Manager (<code>proxy-host-N_access.log</code> e os <code>.gz</code> rotacionados).",
    "q2": "Ele altera meu NPM?",
    "a2": "Não. Ele só lê os logs e o banco do NPM. Proxy hosts, certificados e configuração Advanced ficam intactos.",
    "q3": "É uma ferramenta de web analytics?",
    "a3": "Ele conta requisições que chegaram ao seu proxy, não pessoas. Não há script de rastreamento nem nada para adicionar aos seus sites.",
    "q4": "Dá para rodar sem Docker?",
    "a4": "Sim. Precisa de Python 3.11+ num host que consiga ler os logs do NPM; há uma unit do systemd em <code>deploy/</code>.",
    "cta.title": "Código aberto, licença MIT",
    "cta.d": "Leia o código, abra uma issue ou adapte para o seu próprio proxy. Contribuições são bem-vindas.",
    "cta.gh": "Pegue o RequestAtlas no GitHub",
    "cta.demo": "Veja o gerador de dados de demo",
    "foot.d": "O RequestAtlas é um projeto independente, sem vínculo com o Nginx Proxy Manager. As imagens do painel usam dados de exemplo fictícios.",
    "foot.lic": "Licença MIT",
    "foot.issues": "Issues",
  };
  const EN = { copied: "Copied", copyFail: "Select and copy" };
  for (const el of document.querySelectorAll("[data-i18n]")) EN[el.dataset.i18n] = el.innerHTML;
  for (const el of document.querySelectorAll("[data-i18n-alt]")) EN[el.dataset.i18nAlt] = el.alt;
  const META = {
    en: { title: document.title, desc: document.querySelector('meta[name="description"]').content },
    pt: {
      title: "RequestAtlas — painel de tráfego auto-hospedado para Nginx Proxy Manager",
      desc: "O RequestAtlas lê os logs de acesso do Nginx Proxy Manager e separa páginas, arquivos estáticos e API, bots por User-Agent e 403 intencionais de falhas 5xx reais. Auto-hospedado, código aberto, MIT.",
    },
  };
  let lang = "en";
  function T(key) { return (lang === "pt" ? PT : EN)[key] ?? EN[key]; }
  const toggle = document.querySelector("[data-lang-toggle]");
  function apply(next) {
    lang = next;
    const dict = lang === "pt" ? PT : EN;
    root.lang = lang === "pt" ? "pt-BR" : "en";
    for (const el of document.querySelectorAll("[data-i18n]")) {
      const v = dict[el.dataset.i18n];
      if (v !== undefined) el.innerHTML = v;
    }
    for (const el of document.querySelectorAll("[data-i18n-alt]")) el.alt = dict[el.dataset.i18nAlt];
    document.title = META[lang].title;
    document.querySelector('meta[name="description"]').content = META[lang].desc;
    const other = lang === "pt" ? "en" : "pt";
    toggle.textContent = other.toUpperCase();
    toggle.lang = other;
    toggle.setAttribute("aria-label", other === "pt" ? "PT, ler em português" : "EN, read in English");
  }
  const KEY = "requestatlas-site-lang";
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch {}
  const initial = saved || (/^pt\b/i.test(navigator.language || "") ? "pt" : "en");
  if (initial === "pt") apply("pt");
  toggle.addEventListener("click", () => {
    const next = lang === "pt" ? "en" : "pt";
    apply(next);
    try { localStorage.setItem(KEY, next); } catch {}
  });

  // --- reveal on scroll (CSS only animates when motion is allowed)
  const items = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    items.forEach((el) => el.classList.add("in"));
    return;
  }
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  items.forEach((el) => io.observe(el));
})();
