"use strict";
/* logsNPM — painel de análise dos access logs do Nginx Proxy Manager.
   Todo texto vindo do log (caminhos, UAs, referrers, hosts) passa por esc(): é dado de terceiros.
   Textos da interface passam por t(): a chave é o texto em pt-BR; i18n.js traz as traduções. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const S = {
  cfg: null, meta: null, page: "overview", charts: [], seq: 0, timer: null, lang: "pt-BR",
  f: { site: "", p: "7d", from: "", to: "", tz: "", bot: "", status: "", kind: "" },
  ui: { ovMode: "kind", botMode: "bot", bucket: "auto", evWin: 24, pq: "", psort: "hits", poff: 0 },
  worldLoaded: false,
};
let NF, NC, REGION;

/* ------------------------------------------------------------------ i18n */
function t(key, vars) {
  const dict = (window.I18N || {})[S.lang] || {};
  let s = dict[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? `{${k}}`));
  return s;
}
const botName = (name) => ({ __nonbot__: t("Não identificado como bot"), __noua__: t("Sem User-Agent"), __generic__: t("Outro bot (genérico)") }[name]
  || name.replace("(other)", t("(outros)")));
const groupName = (g) => ({ ai: t("IA"), search: t("Buscador"), seo: "SEO", social: t("Social/preview"), monitoring: t("Monitoramento"),
  scanner: t("Scanner"), tool: t("Ferramenta HTTP"), custom: t("Personalizado"), generic: t("Genérico"), noua: t("Sem User-Agent") }[g] || g || "—");
const STATUS_TEXT = {
  200: "OK", 201: "Created", 204: "No Content", 206: "Partial Content", 207: "Multi-Status", 301: "Moved Permanently", 302: "Found",
  303: "See Other", 304: "Not Modified", 307: "Temporary Redirect", 308: "Permanent Redirect", 400: "Bad Request", 401: "Unauthorized",
  402: "Payment Required", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 408: "Request Timeout", 409: "Conflict",
  410: "Gone", 413: "Payload Too Large", 414: "URI Too Long", 422: "Unprocessable", 423: "Locked", 429: "Too Many Requests",
  444: "Fechada pelo NPM", 494: "Header muito grande", 499: "Cliente desistiu", 500: "Erro interno da aplicação", 501: "Not Implemented",
  502: "Bad Gateway (upstream falhou)", 503: "Service Unavailable", 504: "Gateway Timeout (upstream lento)", 507: "Insufficient Storage",
};

/* ------------------------------------------------------------------ cores (sobrescrevíveis em ui.colors) */
const C = {
  html: "#818cf8", static: "#22d3ee", api: "#fbbf24", other: "#94a3b8",
  bot: "#a78bfa", nonbot: "#34d399", noua: "#f472b6",
  s1: "#64748b", s2: "#34d399", s3: "#60a5fa", s4: "#fbbf24", s5: "#f87171", rule: "#c084fc", s429: "#fb923c",
};
let PALETTE = ["#818cf8", "#22d3ee", "#f472b6", "#fbbf24", "#34d399", "#fb923c", "#a78bfa", "#60a5fa", "#f87171", "#2dd4bf", "#e879f9", "#facc15", "#94a3b8"];
const KIND = () => ({ html: t("Páginas HTML"), static: t("Arquivos estáticos"), api: t("API / dados"), other: t("Outros") });
const KIND_SHORT = () => ({ html: "HTML", static: t("Estático"), api: "API", other: t("Outro") });
const BOTFLAG = () => ({ bot: t("Bots identificados"), nonbot: t("Não identificado como bot"), noua: t("Sem User-Agent") });
const ROUTES = { overview: "", sites: "dominios", bots: "bots", status: "status", pages: "paginas", geo: "origem", health: "dados" };
const PAGE_LABEL = () => ({ overview: t("Visão geral"), sites: t("Domínios"), bots: "Bots", status: t("Status & erros"), pages: t("Páginas"), geo: t("Origem"), health: t("Dados") });
const FILTER_KEYS = ["site", "p", "from", "to", "tz", "bot", "status", "kind"];

/* ------------------------------------------------------------------ utils */
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const n = (v) => NF.format(Math.round(v || 0));
const nc = (v) => (Math.abs(v) >= 10000 ? NC.format(v) : NF.format(Math.round(v || 0)));
const fnum = (v, d = 1) => (v || 0).toLocaleString(S.lang, { maximumFractionDigits: d });
const pct = (a, b) => { a = a || 0; return b ? `${fnum((100 * a) / b, a / b < 0.01 && a ? 2 : 1)}%` : "—"; };
const bytes = (b) => {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = b;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${fnum(v, v < 10 ? 1 : 0)} ${u[i]}`;
};
const flag = (cc) => (cc && /^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map((c) => 127397 + c.charCodeAt(0))) : "🏳️");
const country = (cc) => (!cc ? t("Rede local / sem geolocalização") : (REGION && REGION.of(cc)) || cc);
const sum = (a) => a.reduce((x, y) => x + (y || 0), 0);
const prefixLabel = (p) => ({ private: t("rede local/privada"), invalid: t("inválido") }[p] || p);

function dtf(opts) { return new Intl.DateTimeFormat(S.lang, { timeZone: S.f.tz, ...opts }); }
function fmtDT(sec, withYear = false) {
  if (!sec) return "—";
  return dtf({ day: "2-digit", month: "2-digit", ...(withYear ? { year: "numeric" } : {}), hour: "2-digit", minute: "2-digit" }).format(sec * 1000);
}
function fmtBucket(ms, bucket) {
  if (bucket === "hour") return dtf({ day: "2-digit", month: "2-digit", hour: "2-digit" }).format(ms) + (S.lang === "pt-BR" ? "h" : "");
  return dtf({ weekday: "short", day: "2-digit", month: "2-digit" }).format(ms).replace(".", "");
}
function tzOffsetMs(ms, tz) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    .formatToParts(ms).map((x) => [x.type, x.value]));
  return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - ms;
}
function tzLabel(tz = S.f.tz, at = Date.now()) {
  const off = Math.round(tzOffsetMs(at, tz) / 60000);
  const s = off < 0 ? "−" : "+"; const a = Math.abs(off);
  return `UTC${s}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}
function tzOptionLabel(tz) {
  if (tz.label) return tz.label;
  const city = tz.id === "UTC" ? "UTC" : tz.id.split("/").pop().replace(/_/g, " ");
  return tz.id === "UTC" ? "UTC" : `${city} (${tzLabel(tz.id)})`;
}
function zonedMidnight(dateStr, tz) {
  const [y, m, d] = dateStr.split("-").map(Number);
  let g = Date.UTC(y, m - 1, d);
  for (let i = 0; i < 3; i++) g = Date.UTC(y, m - 1, d) - tzOffsetMs(g, tz);
  return Math.floor(g / 1000);
}
function localDateStr(sec) {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: S.f.tz, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(sec * 1000).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}`;
}
function ago(sec) {
  const d = Math.max(0, Date.now() / 1000 - sec);
  if (d < 90) return `${Math.round(d)} s`;
  if (d < 5400) return `${Math.round(d / 60)} min`;
  if (d < 172800) return `${Math.round(d / 3600)} h`;
  return t("{n} dias", { n: Math.round(d / 86400) });
}
function toast(msg) {
  const el = $("#toast"); el.textContent = msg; el.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (el.hidden = true), 7000);
}

/* ------------------------------------------------------------------ período & API */
function range() {
  const now = Date.now() / 1000;
  const to = Math.ceil(now / 3600) * 3600;
  // 7/30 dias = dias corridos inteiros no fuso escolhido (hoje incluído): as barras diárias não começam pela metade
  const days = (k) => zonedMidnight(localDateStr(now - (k - 1) * 86400), S.f.tz);
  switch (S.f.p) {
    case "24h": return { from: to - 86400, to };
    case "30d": return { from: days(30), to };
    case "all": return { from: (S.meta && S.meta.data_from) || days(30), to };
    case "custom":
      if (S.f.from && S.f.to) {
        const a = zonedMidnight(S.f.from, S.f.tz); const b = zonedMidnight(S.f.to, S.f.tz) + 86400;
        if (b > a) return { from: a, to: Math.min(b, to) };
      }
      return { from: days(7), to };
    default: return { from: days(7), to };
  }
}
function params(extra = {}) {
  const r = range();
  const p = { from: r.from, to: r.to, tz: S.f.tz, site: S.f.site, bot: S.f.bot, status: S.f.status, kind: S.f.kind, ...extra };
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== "" && v != null) q.set(k, v);
  return q.toString();
}
async function getJSON(url) {
  const r = await fetch(url);
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
const api = (name, extra) => getJSON(`api/${name}?${params(extra)}`);
const apiRaw = (name, query = "") => getJSON(`api/${name}${query ? "?" + query : ""}`);

/* ------------------------------------------------------------------ hash/routing */
function pageFromPath(path) {
  const page = Object.entries(ROUTES).find(([, v]) => v === path)?.[0];
  return page && S.cfg.pages.includes(page) ? page : S.cfg.pages[0];
}
function readHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  const q = new URLSearchParams(qs || "");
  S.page = pageFromPath(path || "");
  const saved = JSON.parse(localStorage.getItem("logsnpm-f") || "{}");
  for (const k of FILTER_KEYS) S.f[k] = q.has(k) ? q.get(k) : (k === "tz" || k === "p" ? saved[k] || "" : "");
  if (!S.f.p) S.f.p = S.cfg.default_period;
  if (!/^[\w+\-/]+$/.test(S.f.tz || "") || !S.meta.tzs.some((x) => x.id === S.f.tz)) S.f.tz = S.cfg.default_tz;
}
function go(page, patch = {}) {
  Object.assign(S.f, patch);
  const q = new URLSearchParams();
  for (const k of FILTER_KEYS) if (S.f[k] && !(k === "p" && S.f[k] === S.cfg.default_period) && !(k === "tz" && S.f[k] === S.cfg.default_tz)) q.set(k, S.f[k]);
  localStorage.setItem("logsnpm-f", JSON.stringify({ tz: S.f.tz, p: S.f.p }));
  const h = `#/${ROUTES[page]}${q.toString() ? "?" + q : ""}`;
  if (location.hash === h) render(); else location.hash = h;
}

/* ------------------------------------------------------------------ marca, navegação e filtros */
function applyBranding() {
  const c = S.cfg;
  document.documentElement.lang = S.lang;
  document.title = c.subtitle ? `${c.title} · ${c.subtitle}` : c.title;
  $(".brand-text b").textContent = c.title;
  $(".brand-text small").textContent = c.subtitle || t("análise de tráfego do NPM");
  const [a1, a2] = c.accent && c.accent.length ? [c.accent[0], c.accent[1] || c.accent[0]] : ["#818cf8", "#22d3ee"];
  document.documentElement.style.setProperty("--accent", `linear-gradient(135deg, ${a1} 0%, ${a2} 100%)`);
  document.documentElement.style.setProperty("--a1", a1);
  if (c.logo_url) $(".brand-mark").innerHTML = `<img src="${esc(c.logo_url)}" alt="">`;
  Object.assign(C, c.colors || {});
  if (c.colors && Array.isArray(c.colors.palette)) PALETTE = c.colors.palette;
  const labels = PAGE_LABEL();
  $("#nav").innerHTML = c.pages.map((p) => `<a href="#/${ROUTES[p]}" data-page="${p}">${esc(labels[p])}</a>`).join("") +
    (c.links || []).map((l, i) => `<a href="${esc(l.url)}" class="ext${i === 0 ? " first" : ""}" target="_blank" rel="noopener" title="${esc(l.title || "")}">${esc(l.label)} ↗</a>`).join("");
  $$("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  $$("[data-i18n-title]").forEach((el) => (el.title = t(el.dataset.i18nTitle)));
}
function siteName(id) {
  const s = (S.meta?.sites || []).find((x) => String(x.id) === String(id));
  return s ? siteTitle(s) : `#${id}`;
}
function siteTitle(s) {
  if (s.name) return s.name;
  if (s.type === "fallback") return t("(fallback — host não configurado)");
  if (s.domains.length) return s.domains[0];
  return t("site #{id} (removido do NPM)", { id: s.id });
}
function siteLabel(s) {
  const extra = !s.name && s.domains.length > 1 ? ` (+${s.domains.length - 1})` : "";
  return `${siteTitle(s)}${extra}`;
}
const visibleSites = () => S.meta.sites.filter((s) => !s.hidden);
function buildFilters() {
  const m = S.meta;
  const sites = [...visibleSites()].sort((a, b) => b.hits_7d - a.hits_7d || b.hits_total - a.hits_total);
  const groups = [
    [t("Com tráfego (7 dias)"), sites.filter((s) => !s.deleted && s.hits_7d > 0)],
    [t("Sem tráfego recente"), sites.filter((s) => !s.deleted && s.enabled && !s.hits_7d)],
    [t("Desativados / removidos"), sites.filter((s) => s.deleted || (!s.enabled && !s.hits_7d))],
  ];
  $("#f-site").innerHTML = `<option value="">${t("Todos os domínios")}</option>` + groups.filter((g) => g[1].length).map(([lbl, arr]) =>
    `<optgroup label="${esc(lbl)}">${arr.map((s) => `<option value="${s.id}">${esc(siteLabel(s))} · #${s.id}</option>`).join("")}</optgroup>`).join("");

  const bots = m.bots.filter((b) => b.hits > 0 && b.name !== m.not_bot && b.name !== m.empty_ua).sort((a, b) => b.hits - a.hits);
  const byGroup = {};
  for (const b of bots) (byGroup[b.group || "generic"] ||= []).push(b);
  const gs = m.groups.filter((g) => byGroup[g]);
  $("#f-bot").innerHTML = `<option value="">${t("Todos")}</option>
    <option value="bots">${t("Só bots identificados")}</option>
    <option value="nonbot">${t("Não identificado como bot")}</option>
    <option value="noua">${t("Sem User-Agent")}</option>
    <optgroup label="${t("Grupos")}">${gs.map((g) => `<option value="g:${esc(g)}">${t("Grupo")}: ${esc(groupName(g))}</option>`).join("")}</optgroup>` +
    gs.map((g) => `<optgroup label="${esc(groupName(g))}">${byGroup[g].map((b) => `<option value="b:${b.id}">${esc(botName(b.name))}</option>`).join("")}</optgroup>`).join("");
  $("#f-tz").innerHTML = m.tzs.map((z) => `<option value="${esc(z.id)}">${esc(tzOptionLabel(z))}</option>`).join("");
  $("#f-status").innerHTML = [["", t("Todos")], ["2xx", t("2xx sucesso")], ["3xx", t("3xx redirecionamento")], ["4xx", t("4xx erro do cliente")],
    ["403", t("403 (todos)")], ["403r", t("403 por regra de bloqueio")], ["403o", t("403 outros (sem regra)")], ["404", "404"],
    ["429", t("429 rate limit")], ["5xx", t("5xx falha do servidor")]].map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("");
  $("#f-kind").innerHTML = `<option value="">${t("Todos")}</option>` + Object.entries(KIND()).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("");

  $("#f-site").onchange = (e) => go(S.page === "sites" ? S.cfg.pages[0] : S.page, { site: e.target.value });
  $("#f-bot").onchange = (e) => go(S.page, { bot: e.target.value });
  $("#f-status").onchange = (e) => go(S.page, { status: e.target.value });
  $("#f-kind").onchange = (e) => go(S.page, { kind: e.target.value });
  $("#f-tz").onchange = (e) => go(S.page, { tz: e.target.value });
  $$("#f-period button").forEach((b) => (b.onclick = () => {
    const p = b.dataset.p;
    if (p === "custom") {
      const r = range();
      go(S.page, { p, from: S.f.from || localDateStr(r.from), to: S.f.to || localDateStr(r.to - 1) });
    } else go(S.page, { p, from: "", to: "" });
  }));
  $("#f-from").onchange = $("#f-to").onchange = () => go(S.page, { p: "custom", from: $("#f-from").value, to: $("#f-to").value });
  $("#f-clear").onclick = () => go(S.page, { site: "", bot: "", status: "", kind: "", p: S.cfg.default_period, from: "", to: "" });
  $("#refresh").onclick = () => refresh(true);
}
function syncFilters() {
  $("#f-site").value = S.f.site;
  if ($("#f-site").value !== S.f.site) $("#f-site").value = "";
  $("#f-bot").value = S.f.bot; $("#f-status").value = S.f.status; $("#f-kind").value = S.f.kind; $("#f-tz").value = S.f.tz;
  $$("#f-period button").forEach((b) => b.classList.toggle("on", b.dataset.p === S.f.p));
  $("#f-custom").hidden = S.f.p !== "custom";
  if (S.f.p === "custom") { $("#f-from").value = S.f.from; $("#f-to").value = S.f.to; }
  for (const [id, k] of [["f-site", "site"], ["f-bot", "bot"], ["f-status", "status"], ["f-kind", "kind"]]) $(`#${id}`).closest(".f").classList.toggle("active", !!S.f[k]);
  const q = location.hash.split("?")[1];
  $$("#nav a[data-page]").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === S.page);
    a.href = `#/${ROUTES[a.dataset.page]}${q ? "?" + q : ""}`;
  });
  $("#filters").hidden = S.page === "health";
}
function periodText() {
  const r = range();
  return `${fmtDT(r.from, true)} → ${fmtDT(r.to, true)} · ${tzLabel()}`;
}

/* ------------------------------------------------------------------ live */
function updateLive() {
  const m = S.meta; if (!m) return;
  const el = $("#live");
  const iv = S.cfg.interval || 60;
  const age = Date.now() / 1000 - (m.last_cycle_end || 0);
  el.classList.remove("ok", "stale", "down");
  el.classList.add(age < iv * 3 ? "ok" : age < iv * 15 ? "stale" : "down");
  const hm = m.last_event_ts ? dtf({ hour: "2-digit", minute: "2-digit" }).format(m.last_event_ts * 1000) : "—";
  $(".live-text", el).textContent = m.last_cycle_end ? t("coleta há {a} · último evento {h}", { a: ago(m.last_cycle_end), h: hm }) : t("aguardando primeira coleta");
  el.title = t("Último ciclo de ingestão: {a}\nÚltima linha lida dos logs: {b} ({tz})\nA coleta roda a cada {s} s.", { a: fmtDT(m.last_cycle_end, true), b: fmtDT(m.last_event_ts, true), tz: tzLabel(), s: iv });
}
async function refresh(manual = false) {
  const btn = $("#refresh"); btn.classList.add("spin");
  try {
    S.meta = await apiRaw("meta");
    updateLive();
    await render(true);
  } catch (e) { if (manual) toast(e.message); }
  finally { btn.classList.remove("spin"); }
}

/* ------------------------------------------------------------------ charts */
function disposeCharts() { S.charts.forEach((c) => c.dispose()); S.charts = []; }
function mk(el, opt) {
  if (!el) return null;
  const c = echarts.init(el, null, { renderer: "canvas" });
  c.setOption(opt);
  S.charts.push(c);
  return c;
}
window.addEventListener("resize", () => S.charts.forEach((c) => c.resize()));

const TOOLTIP = {
  backgroundColor: "rgba(9,13,24,.96)", borderColor: "rgba(148,163,184,.22)", borderWidth: 1, padding: [10, 12],
  textStyle: { color: "#e6ebf5", fontSize: 12, fontFamily: "Inter" },
  extraCssText: "border-radius:12px;box-shadow:0 18px 40px -12px rgba(0,0,0,.7);",
};
function axisTooltip(labels) {
  return {
    ...TOOLTIP, trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: "rgba(148,163,184,.06)" } },
    formatter(ps) {
      const rows = ps.filter((p) => p.value).sort((a, b) => b.value - a.value);
      const tot = sum(ps.map((p) => p.value));
      return `<div style="font-weight:600;margin-bottom:6px">${esc(labels[ps[0].dataIndex])}</div>` +
        rows.map((p) => `<div style="display:flex;justify-content:space-between;gap:18px"><span>${p.marker}${esc(p.seriesName)}</span><b>${n(p.value)}</b></div>`).join("") +
        (rows.length > 1 ? `<div style="display:flex;justify-content:space-between;gap:18px;border-top:1px solid rgba(148,163,184,.2);margin-top:6px;padding-top:6px;color:#8b97ad"><span>Total</span><b>${n(tot)}</b></div>` : "") +
        (!rows.length ? `<div style="color:#8b97ad">${t("sem requisições")}</div>` : "");
    },
  };
}
function eventIndex(buckets, ts) {
  const ms = ts * 1000; let idx = -1;
  for (let i = 0; i < buckets.length; i++) if (buckets[i] <= ms) idx = i;
  return idx;
}
function timeChart(el, { buckets, bucket, series, events = [], stack = true, type }) {
  const labels = buckets.map((b) => fmtBucket(b, bucket));
  const kind = type || (bucket === "day" || buckets.length <= 48 ? "bar" : "line");
  const marks = events.map((e) => ({ idx: eventIndex(buckets, e.ts), e })).filter((x) => x.idx >= 0);
  const out = series.map((s, i) => {
    const col = s.color || PALETTE[i % PALETTE.length];
    const base = { name: s.name, data: s.data, itemStyle: { color: col }, emphasis: { focus: "series" } };
    if (stack) base.stack = "t";
    if (kind === "bar") Object.assign(base, { type: "bar", barMaxWidth: 34, barCategoryGap: "28%" });
    else Object.assign(base, {
      type: "line", smooth: 0.35, symbol: "none", lineStyle: { width: 1.6 },
      areaStyle: { opacity: stack ? 0.55 : 0.12, color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: col + "cc" }, { offset: 1, color: col + "18" }]) },
    });
    return base;
  });
  if (out.length && marks.length) {
    out[0].markLine = {
      symbol: ["none", "none"], silent: false, animation: false,
      lineStyle: { color: C.rule, type: [5, 4], width: 1.4 },
      label: { color: "#e9d5ff", fontSize: 11, position: "insideEndTop", formatter: (p) => p.name, backgroundColor: "rgba(88,28,135,.55)", padding: [3, 6], borderRadius: 6 },
      data: marks.map((m) => ({ xAxis: m.idx, name: m.e.title })),
    };
  }
  return mk(el, {
    animationDuration: 500, textStyle: { fontFamily: "Inter" },
    grid: { left: 6, right: 14, top: 40, bottom: 4, containLabel: true },
    legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 14, textStyle: { color: "#8b97ad", fontSize: 12 }, type: "scroll", pageIconColor: "#8b97ad", pageTextStyle: { color: "#8b97ad" } },
    tooltip: axisTooltip(labels),
    xAxis: { type: "category", data: labels, axisLine: { lineStyle: { color: "rgba(148,163,184,.15)" } }, axisTick: { show: false }, axisLabel: { color: "#5b6780", hideOverlap: true, fontSize: 11 } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: "rgba(148,163,184,.07)" } }, axisLabel: { color: "#5b6780", fontSize: 11, formatter: (v) => nc(v) } },
    series: out,
  });
}
function donut(el, items, centerTitle) {
  const total = sum(items.map((i) => i.value));
  return mk(el, {
    textStyle: { fontFamily: "Inter" },
    tooltip: { ...TOOLTIP, trigger: "item", formatter: (p) => `${p.marker}${esc(p.name)}<br><b>${n(p.value)}</b> · ${pct(p.value, total)}` },
    legend: { orient: "vertical", right: 0, top: "middle", icon: "circle", itemWidth: 8, itemHeight: 8, itemGap: 9, textStyle: { color: "#8b97ad", fontSize: 12 },
      formatter: (name) => { const it = items.find((i) => i.name === name); return `${name}  ${pct(it?.value || 0, total)}`; } },
    graphic: [{ type: "text", left: "26%", top: "43%", style: { text: nc(total), fill: "#e6ebf5", font: "700 20px Inter", textAlign: "center" } },
      { type: "text", left: "26%", top: "54%", style: { text: centerTitle || t("requisições"), fill: "#5b6780", font: "11px Inter", textAlign: "center" } }],
    series: [{ type: "pie", radius: ["58%", "80%"], center: ["30%", "50%"], avoidLabelOverlap: true, label: { show: false }, padAngle: 2,
      itemStyle: { borderRadius: 6, borderColor: "rgba(10,15,28,1)", borderWidth: 2 },
      data: items.filter((i) => i.value > 0).map((i) => ({ ...i, itemStyle: { color: i.color } })) }],
  });
}
function spark(data, color, h = 38) {
  if (!data || !data.length) return `<svg class="spk" height="${h}"></svg>`;
  const w = 200; const max = Math.max(...data, 1);
  const pts = data.map((v, i) => [data.length === 1 ? w / 2 : (i * w) / (data.length - 1), h - 3 - ((h - 6) * v) / max]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const id = "g" + Math.random().toString(36).slice(2, 8);
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="${h}">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${line}L${w},${h}L0,${h}Z" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

/* ------------------------------------------------------------------ building blocks */
const ICON_INFO = `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/></svg>`;
const ICON_WARN = `<svg viewBox="0 0 24 24"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/></svg>`;
function notice(html, cls = "") { return `<div class="notice ${cls}">${cls === "warn" ? ICON_WARN : ICON_INFO}<div>${html}</div></div>`; }
function caveat() {
  if (!S.cfg.show_caveats) return "";
  if (S.cfg.caveat_text) return notice(esc(S.cfg.caveat_text));
  return notice(`<b>${t("Requisição a página HTML não é anúncio exibido, receita de AdSense, cadastro nem visitante humano.")}</b>
    ${t("Bots são reconhecidos apenas pelo User-Agent (que pode ser falsificado). O restante aparece como “não identificado como bot” — inclui navegadores reais e robôs que não se declaram.")}`);
}
function reindexWarning() {
  return S.meta.reindex_needed ? notice(t("As regras de classificação mudaram desde a última ingestão. Rode <code>logsnpm reindex</code> para reprocessar os logs com as regras novas."), "warn") : "";
}
function card(title, sub, body, actions = "", cls = "") {
  return `<section class="card ${cls}"><div class="card-h"><div><h3>${title}</h3>${sub ? `<p>${sub}</p>` : ""}</div>${actions}</div><div class="card-b">${body}</div></section>`;
}
function deltaBadge(cur, prev, mode = "neutral") {
  if (!prev && !cur) return `<span class="delta">—</span>`;
  if (!prev) return `<span class="delta">${t("novo")}</span>`;
  const d = (cur - prev) / prev;
  const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "•";
  let cls = "";
  if (Math.abs(d) >= 0.005) cls = mode === "bad" ? (d > 0 ? "up" : "down") : (d > 0 ? "neutral-up" : "neutral-down");
  const v = Math.abs(d * 100);
  const txt = d > 3 ? `${arrow} ${fnum(cur / prev, cur / prev < 10 ? 1 : 0)}×` : `${arrow} ${fnum(v, v < 10 ? 1 : 0)}%`;
  return `<span class="delta ${cls}" title="${esc(t("vs. período anterior de mesma duração ({n})", { n: n(prev) }))}">${txt}</span>`;
}
function kpi({ label, value, color, sub = "", delta = "", sparkData, title = "" }) {
  return `<div class="card kpi" style="--c:${color}" title="${esc(title)}">
    <div class="lbl"><i></i>${label}</div><div class="val">${value}</div>
    <div class="sub"><span>${sub}</span>${delta}</div>
    ${sparkData ? `<div class="spark">${spark(sparkData, color)}</div>` : ""}</div>`;
}
function pill({ label, value, color, sub = "", title = "", est = false }) {
  return `<div class="card pill" style="--c:${color}" title="${esc(title)}"><div class="lbl"><i></i>${label}${est ? `<span class="est">${t("estimativa")}</span>` : ""}</div>
    <div class="val">${value}</div><div class="sub">${sub}</div></div>`;
}
function seg(id, options, current) {
  return `<div class="seg small" data-seg="${id}">${options.map(([v, l]) => `<button data-v="${esc(v)}" class="${String(v) === String(current) ? "on" : ""}">${esc(l)}</button>`).join("")}</div>`;
}
const bucketSeg = () => seg("bucket", [["auto", "Auto"], ["hour", t("Hora")], ["day", t("Dia")]], S.ui.bucket);
function bindSeg(root, id, fn) { $$(`[data-seg="${id}"] button`, root).forEach((b) => (b.onclick = () => fn(b.dataset.v))); }
function barCell(v, max, color = "rgba(129,140,248,.4)") {
  const w = max ? Math.max(2, (100 * v) / max) : 0;
  return `<td class="barcell"><div class="bar" style="width:${w}%;background:linear-gradient(90deg,${color},transparent)"></div><span>${n(v)}</span></td>`;
}
function statusStack(r) {
  const tot = (r.s2xx || 0) + (r.s3xx || 0) + (r.s4xx || 0) + (r.s5xx || 0);
  if (!tot) return `<div class="stack"></div>`;
  const sg = (v, c, l) => (v ? `<i style="width:${(100 * v) / tot}%;background:${c}" title="${l}: ${n(v)}"></i>` : "");
  return `<div class="stack" title="2xx ${n(r.s2xx)} · 3xx ${n(r.s3xx)} · 4xx ${n(r.s4xx)} · 5xx ${n(r.s5xx)}">${sg(r.s2xx, C.s2, "2xx")}${sg(r.s3xx, C.s3, "3xx")}${sg(r.s4xx, C.s4, "4xx")}${sg(r.s5xx, C.s5, "5xx")}</div>`;
}
function kindBadge(k) { return `<span class="badge b-${k}">${KIND_SHORT()[k] || k}</span>`; }
function botBadge(name) {
  const cls = name === S.meta.not_bot ? "b-nonbot" : name === S.meta.empty_ua ? "b-noua" : "b-bot";
  return `<span class="badge ${cls}">${esc(botName(name))}</span>`;
}
function ignoredNote(ign) {
  if (!ign || !ign.length) return "";
  const m = { host: "host", kind: t("tipo"), status: "status", "status-exato": t("código exato (usa a classe, ex.: 4xx)"), "status-regra": t("regra do 403 (mostra todos os 403)") };
  return `<div class="ignored">${t("Filtro sem efeito neste quadro:")} ${ign.map((x) => m[x] || x).join(", ")}.</div>`;
}
function head(title, desc, extra = "") {
  return `<div class="page-head"><div><h1>${title}</h1>${desc ? `<p>${desc}</p>` : ""}</div><div class="meta">${extra || periodText()}</div></div>`;
}
function skeleton() {
  return `<div class="page-head"><div><div class="skel" style="width:260px;height:30px"></div><div class="skel" style="width:420px;height:14px;margin-top:10px"></div></div></div>
    <div class="grid g-kpi">${'<div class="card kpi"><div class="skel" style="height:86px"></div></div>'.repeat(5)}</div>
    <div class="card mt"><div class="card-b"><div class="skel" style="height:340px;margin-top:14px"></div></div></div>`;
}

/* ------------------------------------------------------------------ render */
async function render(silent = false) {
  if (!S.meta) return;
  readHash();
  syncFilters();
  const seq = ++S.seq;
  const app = $("#app");
  if (!silent) { disposeCharts(); app.innerHTML = skeleton(); } else app.classList.add("loading-veil");
  try {
    const fn = { overview: pageOverview, sites: pageSites, bots: pageBots, status: pageStatus, pages: pagePages, geo: pageGeo, health: pageHealth }[S.page];
    const view = await fn();
    if (seq !== S.seq) return;
    const y = window.scrollY;
    disposeCharts();
    app.innerHTML = view.html;
    view.mount && view.mount(app);
    if (silent) window.scrollTo(0, y);
  } catch (e) {
    if (seq !== S.seq) return;
    app.innerHTML = `<div class="card"><div class="empty">${t("Falha ao carregar:")} ${esc(e.message)}</div></div>`;
    console.error(e);
  } finally { app.classList.remove("loading-veil"); }
}

function siteHero() {
  if (!S.f.site) return "";
  const s = S.meta.sites.find((x) => String(x.id) === S.f.site);
  if (!s) return "";
  const rules = s.ua_rules.map((r) => `<span class="badge b-rule" title="${esc(t("Lido do Advanced do proxy host (somente leitura)"))}">UA ~* ${esc(r.pattern)} → ${r.status}</span>`).join("");
  const aliases = s.name ? s.domains : s.domains.slice(1);
  return `<section class="card hero"><div>
      <div class="dim small">Proxy host #${s.id}${s.type !== "proxy" ? ` · ${esc(s.type)}` : ""}</div>
      <h2>${esc(siteTitle(s))}</h2>
      <div class="chips">${aliases.map((d) => `<span class="badge">${esc(d)}</span>`).join("")}
        ${s.enabled ? `<span class="badge b-ok"><i></i>${t("ativo")}</span>` : `<span class="badge b-off">${t("desativado")}</span>`}
        ${s.ssl ? `<span class="badge b-ok">SSL</span>` : ""}${rules}</div></div>
    <div class="facts">${s.forward ? `<div><b class="mono">${esc(s.forward)}</b>${t("destino (upstream)")}</div>` : ""}
      <div><b>${n(s.hits_7d)}</b>${t("requisições em 7 dias")}</div>
      <div><b>${s.last_hour ? fmtDT(s.last_hour + 3600) : "—"}</b>${t("última atividade")}</div>
      <div><a href="#/" data-clear-site>✕ ${t("ver todos os domínios")}</a></div></div></section>`;
}
function bindHero(root) { const a = $("[data-clear-site]", root); if (a) a.onclick = (e) => { e.preventDefault(); go(S.page, { site: "" }); }; }
function bindGo(root) { $$("[data-go]", root).forEach((a) => (a.onclick = (e) => { e.preventDefault(); go(a.dataset.go); })); }
const hasPage = (p) => S.cfg.pages.includes(p);

/* ---- Visão geral */
async function pageOverview() {
  const mode = S.ui.ovMode;
  const [ov, ts] = await Promise.all([api("overview", { bucket: S.ui.bucket }), mode === "sclass" ? api("timeseries", { group: "sclass", bucket: S.ui.bucket }) : null]);
  const T = ov.totals; const P = ov.previous;
  const ks = ov.kind_series; const K = KIND();
  const title = S.f.site ? `${t("Visão geral")} · ${esc(siteName(S.f.site))}` : t("Visão geral");
  const s4other = (T.s4xx || 0) - (T.s403_rule || 0);
  const ofTotal = (v) => t("{p} do total", { p: pct(v, T.total) });
  const html = `${head(title, t("Requisições lidas dos access logs dos proxy hosts do NPM (arquivo ativo + rotacionados). Métricas separadas por tipo; nada aqui é contagem de pessoas."))}
    ${reindexWarning()}${siteHero()}${caveat()}
    <div class="grid g-kpi">
      ${kpi({ label: t("Requisições totais"), value: n(T.total), color: "#e2e8f0", sub: t("{b} enviados", { b: bytes(T.bytes) }), delta: deltaBadge(T.total, P.total), sparkData: sumSeries(Object.values(ks)), title: t("Toda linha válida do log no período") })}
      ${kpi({ label: K.html, value: n(T.html), color: C.html, sub: ofTotal(T.html), delta: deltaBadge(T.html, P.html), sparkData: ks.html, title: t("GET/HEAD a caminhos sem extensão ou .html/.php. Não é pageview de pessoa.") })}
      ${kpi({ label: K.static, value: n(T.static), color: C.static, sub: ofTotal(T.static), delta: deltaBadge(T.static, P.static), sparkData: ks.static, title: t("JS, CSS, imagens, fontes, mídia") })}
      ${kpi({ label: K.api, value: n(T.api), color: C.api, sub: ofTotal(T.api), delta: deltaBadge(T.api, P.api), sparkData: ks.api, title: t("/api/, hosts api.*, WebDAV/OCS, métodos de escrita") })}
      ${kpi({ label: K.other, value: n(T.other), color: C.other, sub: "robots, sitemap, .well-known, OPTIONS", delta: deltaBadge(T.other, P.other), sparkData: ks.other })}
    </div>
    <div class="grid g-pill mt">
      ${pill({ label: t("Bots identificados"), value: n(T.bot), color: C.bot, sub: t("{a} do total · {b} do HTML", { a: pct(T.bot, T.total), b: pct(T.bot_html, T.html) }), title: t("User-Agent casou com uma assinatura de robô") })}
      ${pill({ label: t("Não identificado como bot"), value: n(T.nonbot), color: C.nonbot, sub: t("{a} do total · não é “humano”", { a: pct(T.nonbot, T.total) }), title: t("Nenhuma assinatura de robô no User-Agent. Inclui navegadores e robôs disfarçados.") })}
      ${pill({ label: t("Sem User-Agent"), value: n(T.noua), color: C.noua, sub: ofTotal(T.noua) })}
      ${pill({ label: t("403 por regra de bloqueio"), value: n(T.s403_rule), color: C.rule, sub: t("intencional — esperado"), title: t("403 cujo UA casa a regra do Advanced do proxy host, ou IP bloqueado no geo do NPM") })}
      ${pill({ label: t("Outros 4xx"), value: n(s4other), color: C.s4, sub: `404: ${n(T.s404)} · ${t("403 s/ regra")}: ${n(T.s403_other)} · 499: ${n(T.s499)}` })}
      ${pill({ label: t("429 rate limit"), value: n(T.s429), color: C.s429, sub: pct(T.s429, T.total) })}
      ${pill({ label: t("5xx falha"), value: n(T.s5xx), color: C.s5, sub: t("{a} · {b} sem upstream", { a: pct(T.s5xx, T.total), b: n(T.s5xx_noup) }), title: t("Indisponibilidade/erro real: 500 da aplicação, 502/504 do upstream") })}
      ${pill({ label: t("IPs distintos"), value: n(ov.ips.public), color: "#94a3b8", sub: t("públicos · não são pessoas"), est: true, title: t("Contagem de IPs públicos distintos (hash). NAT, proxies, IPv6 rotativo e bots com muitos IPs distorcem. Nunca equivale a visitantes.") })}
    </div>
    <section class="card mt"><div class="card-h"><div><h3>${ov.bucket === "hour" ? t("Requisições por hora") : t("Requisições por dia")}</h3><p>${t("Linhas tracejadas = eventos registrados. Fuso: {tz}.", { tz: esc(tzLabel()) })}</p></div>
      <div class="toolbar">${seg("ovmode", [["kind", t("Por tipo")], ["botflag", t("Bot × não identificado")], ["sclass", t("Por status")]], mode)}${bucketSeg()}</div></div>
      <div class="card-b"><div class="chart tall" id="ch-main"></div></div></section>
    <div class="grid g-21 mt">
      ${S.f.site ? card(t("Páginas mais solicitadas"), t("Top 12 no período com os filtros atuais"), `<div id="top-pages"><div class="skel" style="height:300px"></div></div>`, hasPage("pages") ? `<a class="btn ghost" data-go="pages">${t("ver todas")} →</a>` : "")
        : card(t("Domínios"), t("Clique para filtrar. Tendência = mesmas granularidade e período do gráfico."), sitesTable(ov.sites), hasPage("sites") ? `<a class="btn ghost" data-go="sites">cards →</a>` : "")}
      <div class="grid">
        ${card(t("Status HTTP"), t("403 de regra separado dos demais 4xx"), `<div class="chart short" id="ch-status"></div>`)}
        ${card(t("Top bots"), t("Por User-Agent"), topBotsList(ov.top_bots, T.total), hasPage("bots") ? `<a class="btn ghost" data-go="bots">${t("detalhes")} →</a>` : "")}
      </div>
    </div>`;
  return {
    html,
    mount(root) {
      bindHero(root); bindGo(root);
      bindSeg(root, "ovmode", (v) => { S.ui.ovMode = v; render(true); });
      bindSeg(root, "bucket", (v) => { S.ui.bucket = v; render(true); });
      $$("tr[data-site]", root).forEach((tr) => (tr.onclick = () => go("overview", { site: tr.dataset.site })));
      let series;
      const BF = BOTFLAG();
      if (mode === "kind") series = Object.keys(K).map((k) => ({ name: K[k], data: ks[k], color: C[k] }));
      else if (mode === "botflag") series = ["bot", "nonbot", "noua"].map((k) => ({ name: BF[k], data: ov.botflag_series[k], color: C[k] }));
      else series = ts.series.map((s) => ({ name: s.name, data: s.data, color: C["s" + s.key] || "#64748b" }));
      timeChart($("#ch-main", root), { buckets: ov.buckets, bucket: ov.bucket, series, events: ov.events });
      donut($("#ch-status", root), [
        { name: "2xx", value: T.s2xx || 0, color: C.s2 }, { name: "3xx", value: T.s3xx || 0, color: C.s3 },
        { name: t("403 por regra"), value: T.s403_rule || 0, color: C.rule }, { name: t("4xx (outros)"), value: s4other, color: C.s4 },
        { name: "5xx", value: T.s5xx || 0, color: C.s5 },
      ]);
      if (S.f.site) api("pages", { limit: 12 }).then((pg) => { const el = $("#top-pages", root); if (el) el.innerHTML = pagesTable(pg.rows, pg.total_hits, 0, true); }).catch((e) => toast(e.message));
    },
  };
}
function sumSeries(arrs) { if (!arrs.length) return []; return arrs[0].map((_, i) => sum(arrs.map((a) => a[i]))); }
function sitesTable(rows) {
  const hidden = new Set(S.meta.sites.filter((s) => s.hidden).map((s) => s.id));
  rows = rows.filter((r) => !hidden.has(r.site));
  if (!rows.length) return `<div class="empty">${t("Sem requisições no período.")}</div>`;
  const max = Math.max(...rows.map((r) => r.hits));
  return `<div class="scroll short" style="margin:0 -18px"><table class="tbl"><thead><tr><th>${t("Domínio")}</th><th>${t("Tendência")}</th><th class="num">${t("Requisições")}</th><th class="num">HTML</th><th class="num">% bots</th><th class="num">${t("403 regra")}</th><th class="num">5xx</th></tr></thead><tbody>
    ${rows.map((r) => {
      const s = S.meta.sites.find((x) => x.id === r.site);
      return `<tr class="click" data-site="${r.site}"><td><div style="font-weight:600">${esc(s ? siteTitle(s) : "#" + r.site)}</div><div class="host">#${r.site}${s && s.domains.length > 1 ? ` · +${s.domains.length - 1} alias` : ""}</div></td>
        <td style="width:150px">${spark(r.spark, C.html, 30)}</td>${barCell(r.hits, max)}<td class="num">${n(r.html)}</td>
        <td class="num">${pct(r.bot, r.hits)}</td><td class="num">${r.s403_rule ? `<span class="badge b-rule">${n(r.s403_rule)}</span>` : "—"}</td>
        <td class="num">${r.s5xx ? `<span class="badge b-err">${n(r.s5xx)}</span>` : "—"}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}
function topBotsList(rows, total) {
  if (!rows.length) return `<div class="empty">${t("Nenhum bot identificado no período.")}</div>`;
  const max = rows[0].hits;
  return `<table class="tbl" style="margin:0 -6px"><tbody>${rows.map((r) => `<tr><td><b>${esc(botName(r.name))}</b><div class="host">${esc(groupName(r.group))}</div></td>
    ${barCell(r.hits, max, "rgba(167,139,250,.45)")}<td class="num dim">${pct(r.hits, total)}</td></tr>`).join("")}</tbody></table>`;
}

/* ---- Domínios */
async function pageSites() {
  const ov = await api("overview", { site: "" });
  const by = Object.fromEntries(ov.sites.map((s) => [s.site, s]));
  const all = visibleSites().map((s) => ({ s, d: by[s.id] || { hits: 0, html: 0, bot: 0, s5xx: 0, s403_rule: 0, spark: [] } }));
  const active = all.filter((x) => x.d.hits > 0).sort((a, b) => b.d.hits - a.d.hits);
  const idle = all.filter((x) => !x.d.hits && !x.s.deleted && x.s.enabled);
  const off = all.filter((x) => !x.d.hits && (x.s.deleted || !x.s.enabled));
  const cardOf = ({ s, d }) => `<div class="card site ${s.deleted || !s.enabled ? "off" : ""}" data-site="${s.id}">
      <div class="top"><div><h4>${esc(siteTitle(s))}</h4><div class="alias">${(s.name ? s.domains : s.domains.slice(1)).map(esc).join(" · ") || "&nbsp;"}</div></div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end">
          ${s.deleted ? `<span class="badge b-off">${t("removido")}</span>` : s.enabled ? `<span class="badge b-ok"><i></i>${t("ativo")}</span>` : `<span class="badge b-off">${t("desativado")}</span>`}
          ${s.ua_rules.length ? `<span class="badge b-rule" title="${esc(s.ua_rules.map((r) => r.pattern + " → " + r.status).join("; "))}">${t("bloqueio UA")}</span>` : ""}
          <span class="badge">#${s.id}</span></div></div>
      ${s.forward ? `<div class="fwd">→ ${esc(s.forward)}</div>` : `<div class="fwd">${esc(s.type)}</div>`}
      <div class="nums"><div><b>${nc(d.hits)}</b><span>${t("requisições")}</span></div><div><b>${nc(d.html)}</b><span>HTML</span></div>
        <div><b>${pct(d.bot, d.hits)}</b><span>bots</span></div><div><b style="color:${d.s5xx ? C.s5 : "inherit"}">${nc(d.s5xx)}</b><span>5xx</span></div></div>
      <div class="spark">${spark(d.spark, d.hits ? C.html : "#334155", 44)}</div></div>`;
  const html = `${head(t("Domínios"), t("Todos os proxy hosts configurados no NPM (lidos do banco em modo somente leitura). Clique em um domínio para abrir a visão geral filtrada."))}
    <div class="section-title">${t("Com tráfego no período")} · ${active.length}</div>
    <div class="site-grid">${active.map(cardOf).join("") || `<div class="empty">${t("Nenhum.")}</div>`}</div>
    ${idle.length ? `<div class="section-title">${t("Ativos sem tráfego no período")} · ${idle.length}</div><div class="site-grid">${idle.map(cardOf).join("")}</div>` : ""}
    ${off.length ? `<div class="section-title">${t("Desativados / removidos no NPM")} · ${off.length}</div>
      <div class="card" style="padding:14px 16px;display:flex;flex-wrap:wrap;gap:8px">${off.map(({ s }) => `<span class="badge b-off" data-site="${s.id}" style="cursor:pointer;padding:5px 10px" title="${esc(s.forward || "")}">${esc(siteLabel(s))} · #${s.id} · ${s.deleted ? t("removido") : t("desativado")}</span>`).join("")}</div>` : ""}`;
  return { html, mount(root) { $$("[data-site]", root).forEach((c) => (c.onclick = () => go(S.cfg.pages[0], { site: c.dataset.site }))); } };
}

/* ---- Bots */
async function pageBots() {
  const mode = S.ui.botMode;
  const tsBot = mode === "botflag" ? S.f.bot : (S.f.bot || "bots");
  const uaBot = S.f.bot && S.f.bot !== "bots" ? S.f.bot : "nonbot";
  const [b, ts, uas] = await Promise.all([
    api("bots"), api("timeseries", { group: mode, bot: tsBot, bucket: S.ui.bucket, top: 10 }), api("uas", { bot: uaBot, limit: 30 }),
  ]);
  const bots = b.table.filter((r) => r.is_bot);
  const botHits = sum(bots.map((r) => r.hits));
  const botHtml = sum(bots.map((r) => r.html));
  const allHtml = sum(b.table.map((r) => r.html));
  const nonbot = b.table.find((r) => r.name === S.meta.not_bot);
  const top = bots[0];
  const max = Math.max(...b.table.map((r) => r.hits), 1);
  const rowFilter = (r) => (r.is_bot ? "b:" + r.id : r.name === S.meta.not_bot ? "nonbot" : "noua");
  const html = `${head("Bots", t("Identificação exclusivamente pelo User-Agent (campo logo após <code>[Sent-to …]</code>), nunca pela URL ou referrer. “Não identificado como bot” não significa humano."))}
    ${siteHero()}
    <div class="grid g-kpi">
      ${kpi({ label: t("Requisições de bots"), value: n(botHits), color: C.bot, sub: t("{p} do total", { p: pct(botHits, b.total) }) })}
      ${kpi({ label: t("Páginas HTML pedidas por bots"), value: n(botHtml), color: C.html, sub: t("{p} de todo HTML", { p: pct(botHtml, allHtml) }) })}
      ${kpi({ label: t("Não identificado como bot"), value: n(nonbot?.hits || 0), color: C.nonbot, sub: t("{p} · navegadores + robôs não declarados", { p: pct(nonbot?.hits || 0, b.total) }) })}
      ${kpi({ label: t("Bots distintos"), value: n(bots.length), color: "#e2e8f0", sub: t("assinaturas que apareceram") })}
      ${kpi({ label: top ? t("Maior: {b}", { b: esc(botName(top.name)) }) : t("Maior bot"), value: top ? n(top.hits) : "—", color: C.rule, sub: top ? t("{p} do total · {r} barrados por regra", { p: pct(top.hits, b.total), r: n(top.s403_rule) }) : "" })}
    </div>
    <section class="card mt"><div class="card-h"><div><h3>${ts.bucket === "hour" ? t("Evolução horária") : t("Evolução diária")}</h3><p>${mode === "botflag" ? t("Todo o tráfego, separado por identificação") : t("Somente bots identificados (top 10 + outros)")}. ${t("Fuso")} ${esc(tzLabel())}.</p></div>
      <div class="toolbar">${seg("botmode", [["bot", t("Por bot")], ["botgroup", t("Por grupo")], ["botflag", t("Bot × não identificado")]], mode)}${bucketSeg()}</div></div>
      <div class="card-b"><div class="chart tall" id="ch-bots"></div></div></section>
    <div class="mt">
      ${card(t("Todos os bots"), t("Clique numa linha para filtrar o painel inteiro por esse bot"), `<div class="scroll" style="margin:0 -18px"><table class="tbl"><thead><tr>
        <th>Bot</th><th class="num">${t("Requisições")}</th><th class="num">%</th><th class="num">HTML</th><th class="num">${t("Estát.")}</th><th class="num">API</th>
        <th class="num">2xx</th><th class="num">${t("403 regra")}</th><th class="num">${t("403 outros")}</th><th class="num">429</th><th class="num">5xx</th><th class="num">IPs <span class="est">≈</span></th><th>${t("Visto")}</th></tr></thead><tbody>
        ${b.table.map((r) => `<tr class="click" data-bot="${rowFilter(r)}">
          <td><b>${esc(botName(r.name))}</b><div class="host">${r.is_bot ? esc(groupName(r.group)) : r.name === S.meta.not_bot ? t("não é sinônimo de humano") : t("header ausente")}</div></td>
          ${barCell(r.hits, max, r.is_bot ? "rgba(167,139,250,.45)" : r.name === S.meta.not_bot ? "rgba(52,211,153,.35)" : "rgba(244,114,182,.35)")}
          <td class="num dim">${pct(r.hits, b.total)}</td><td class="num">${n(r.html)}</td><td class="num">${n(r.static)}</td><td class="num">${n(r.api)}</td>
          <td class="num">${n(r.s2xx)}</td><td class="num">${r.s403_rule ? `<span class="badge b-rule">${n(r.s403_rule)}</span>` : "—"}</td>
          <td class="num">${r.s403_other ? n(r.s403_other) : "—"}</td><td class="num">${r.s429 ? n(r.s429) : "—"}</td>
          <td class="num">${r.s5xx ? `<span class="badge b-err">${n(r.s5xx)}</span>` : "—"}</td><td class="num">${n(r.ips)}</td>
          <td class="nowrap small dim">${fmtDT(r.first)}<br>${fmtDT(r.last)}</td></tr>`).join("")}</tbody></table></div>`)}
    </div>
    <div class="grid g-12 mt">
      ${card(t("Por grupo"), t("IA, buscadores, SEO, scanners…"), `<div class="chart" id="ch-groups"></div>`)}
      ${card(uaBot === "nonbot" ? t("User-Agents mais frequentes sem assinatura de bot") : t("User-Agents do filtro atual"),
        uaBot === "nonbot" ? t("Útil para achar robôs que não se declaram: UAs repetitivos, antigos ou genéricos com volume alto.") : t("Variações de UA que caíram nesse filtro"), uaTable(uas))}
    </div>`;
  return {
    html,
    mount(root) {
      bindSeg(root, "botmode", (v) => { S.ui.botMode = v; render(true); });
      bindSeg(root, "bucket", (v) => { S.ui.bucket = v; render(true); });
      bindHero(root);
      $$("tr[data-bot]", root).forEach((tr) => (tr.onclick = () => go("bots", { bot: tr.dataset.bot })));
      const BF = BOTFLAG();
      const series = ts.series.map((s, i) => {
        if (mode === "botflag") return { name: BF[s.key] || s.name, data: s.data, color: C[s.key] };
        const name = s.key === "_rest" ? t("outros") : mode === "botgroup" ? (s.key === S.meta.not_bot ? botName(s.key) : groupName(s.key)) : botName(s.name);
        return { name, data: s.data, color: s.key === "_rest" ? "#475569" : PALETTE[i % PALETTE.length] };
      });
      timeChart($("#ch-bots", root), { buckets: ts.buckets, bucket: ts.bucket, series, events: ts.events });
      const gcolors = { ai: "#a78bfa", search: "#22d3ee", seo: "#fbbf24", social: "#f472b6", monitoring: "#60a5fa", scanner: "#f87171", tool: "#fb923c", custom: "#2dd4bf", generic: "#94a3b8", noua: "#e879f9" };
      donut($("#ch-groups", root), b.groups.map((g) => ({ name: g.group === S.meta.not_bot || g.group === S.meta.empty_ua ? botName(g.group) : groupName(g.group), value: g.hits, color: g.group === S.meta.not_bot ? C.nonbot : g.group === S.meta.empty_ua ? C.noua : gcolors[g.group] || "#64748b" })));
    },
  };
}
function uaTable(uas) {
  if (!uas.rows.length) return `<div class="empty">${t("Nada no período.")}</div>`;
  const max = uas.rows[0].hits;
  return `<div class="scroll short" style="margin:0 -18px"><table class="tbl"><thead><tr><th>User-Agent</th><th>${t("Classificação")}</th><th class="num">${t("Requisições")}</th></tr></thead><tbody>
    ${uas.rows.map((r) => `<tr><td class="ua">${esc(r.ua || t("(vazio)"))}</td><td class="nowrap">${botBadge(r.bot)}</td>${barCell(r.hits, max)}</tr>`).join("")}
    </tbody></table></div>${ignoredNote(uas.ignored)}`;
}

/* ---- Status */
async function pageStatus() {
  const [st, tsC, ts403, ts5, ev] = await Promise.all([
    api("status"), api("timeseries", { group: "sclass", bucket: S.ui.bucket }), api("timeseries", { group: "status403", bucket: S.ui.bucket }),
    api("timeseries", { group: "s5xx", bucket: S.ui.bucket }), apiRaw("events", `window=${S.ui.evWin}`),
  ]);
  const tot = sum(st.codes.map((c) => c.hits));
  const cls = (lo, hi) => sum(st.codes.filter((c) => c.status >= lo && c.status <= hi).map((c) => c.hits));
  const rule = sum(st.codes.map((c) => c.rule_ua + c.rule_ip));
  const s403 = st.codes.find((c) => c.status === 403)?.hits || 0;
  const s429 = st.codes.find((c) => c.status === 429)?.hits || 0;
  const s5 = cls(500, 599);
  const evs = ev.events.filter((e) => !S.f.site || e.event.site == null || String(e.event.site) === S.f.site);
  const html = `${head(t("Status & erros"), t("403 por regra de bloqueio é comportamento esperado e aparece em roxo. Indisponibilidade se mede por 5xx — e 502/504 indicam upstream fora/lento."))}
    ${siteHero()}
    <div class="grid g-pill">
      ${pill({ label: t("2xx sucesso"), value: n(cls(200, 299)), color: C.s2, sub: pct(cls(200, 299), tot) })}
      ${pill({ label: t("3xx redirecionamento"), value: n(cls(300, 399)), color: C.s3, sub: pct(cls(300, 399), tot) })}
      ${pill({ label: t("403 por regra"), value: n(rule), color: C.rule, sub: t("bloqueio intencional") })}
      ${pill({ label: t("403 sem regra"), value: n(s403 - rule), color: C.s4, sub: t("negado pelo upstream/app") })}
      ${pill({ label: t("Outros 4xx"), value: n(cls(400, 499) - s403 - s429), color: C.s4, sub: "404, 401, 499…" })}
      ${pill({ label: t("429 rate limit"), value: n(s429), color: C.s429, sub: pct(s429, tot) })}
      ${pill({ label: t("5xx falha"), value: n(s5), color: C.s5, sub: t("{p} do total", { p: pct(s5, tot) }) })}
    </div>
    ${evs.length ? `<div class="section-title">${t("Antes × depois dos eventos · janela")} ${seg("evwin", [[6, "6 h"], [24, "24 h"], [72, "72 h"], [168, t("7 dias")]], S.ui.evWin)}</div>
      <div class="grid g-2">${evs.map(eventCard).join("")}</div>` : ""}
    <section class="card mt"><div class="card-h"><div><h3>${tsC.bucket === "hour" ? t("Classes de status por hora") : t("Classes de status por dia")}</h3><p>${t("Fuso")} ${esc(tzLabel())}. ${t("Tracejado = evento.")}</p></div>${bucketSeg()}</div>
      <div class="card-b"><div class="chart" id="ch-cls"></div></div></section>
    <div class="grid g-2 mt">
      ${card(t("403: regra × outros"), t("Roxo = regra do NPM (UA/IP). Âmbar = negado pela aplicação."), `<div class="chart" id="ch-403"></div>`)}
      ${card(t("5xx por código"), t("“sem upstream” = o NPM respondeu sozinho (upstream não respondeu)"), `<div class="chart" id="ch-5xx"></div>`)}
    </div>
    <div class="grid g-21 mt">
      ${card(t("Códigos no período"), "", `<div class="scroll" style="margin:0 -18px"><table class="tbl"><thead><tr><th>${t("Código")}</th><th>${t("Significado")}</th><th class="num">${t("Requisições")}</th><th class="num">%</th><th class="num">HTML</th><th class="num">% bots</th><th class="num">${t("Por regra")}</th><th class="num">${t("Sem upstream")}</th></tr></thead><tbody>
        ${st.codes.map((c) => `<tr class="${hasPage("pages") ? "click" : ""}" data-status="${c.status}"><td><span class="badge ${c.status >= 500 ? "b-err" : c.status === 403 && c.rule_ua + c.rule_ip ? "b-rule" : c.status >= 400 ? "b-warn" : c.status >= 300 ? "" : "b-ok"}">${c.status}</span></td>
          <td class="muted">${esc(t(STATUS_TEXT[c.status] || ""))}</td>${barCell(c.hits, st.codes[0]?.hits)}<td class="num dim">${pct(c.hits, tot)}</td><td class="num">${n(c.html)}</td><td class="num">${pct(c.bot, c.hits)}</td>
          <td class="num">${c.rule_ua + c.rule_ip ? `<span class="badge b-rule">${n(c.rule_ua + c.rule_ip)}</span>` : "—"}</td><td class="num">${c.no_upstream ? n(c.no_upstream) : "—"}</td></tr>`).join("")}</tbody></table></div>`)}
      <div class="grid">
        ${card(t("Quem recebe 403"), t("Por classificação de UA"), st.by_bot_403.length ? `<table class="tbl" style="margin:0 -6px"><tbody>${st.by_bot_403.map((r) => `<tr><td>${esc(botName(r.name))}</td><td class="num">${n(r.hits)}</td><td class="num">${r.rule ? `<span class="badge b-rule">${t("{n} regra", { n: n(r.rule) })}</span>` : `<span class="dim">${t("sem regra")}</span>`}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">${t("Nenhum 403.")}</div>`)}
        ${card(t("Caminhos com 5xx"), "Top 15", st.paths_5xx.length ? `<table class="tbl" style="margin:0 -6px"><tbody>${st.paths_5xx.map((r) => `<tr><td><div class="path">${esc(r.path)}</div><div class="host">${esc(r.host)}</div></td><td><span class="badge b-err">${r.status}</span></td><td class="num">${n(r.hits)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">${t("Nenhum 5xx no período")} 🎉</div>`)}
      </div>
    </div>`;
  return {
    html,
    mount(root) {
      bindHero(root);
      bindSeg(root, "bucket", (v) => { S.ui.bucket = v; render(true); });
      bindSeg(root, "evwin", (v) => { S.ui.evWin = +v; render(true); });
      if (hasPage("pages")) $$("tr[data-status]", root).forEach((tr) => (tr.onclick = () => go("pages", { status: tr.dataset.status })));
      timeChart($("#ch-cls", root), { buckets: tsC.buckets, bucket: tsC.bucket, events: tsC.events, series: tsC.series.map((s) => ({ name: s.name, data: s.data, color: C["s" + s.key] || "#64748b" })) });
      const c403 = { "regra UA": [C.rule, t("403 por regra de UA")], "regra IP": ["#e879f9", t("403 por IP bloqueado")], "outros 403": [C.s4, t("403 sem regra")] };
      timeChart($("#ch-403", root), { buckets: ts403.buckets, bucket: ts403.bucket, events: ts403.events, series: ts403.series.map((s) => ({ name: (c403[s.name] || [0, s.name])[1], data: s.data, color: (c403[s.name] || [C.s4])[0] })) });
      timeChart($("#ch-5xx", root), { buckets: ts5.buckets, bucket: ts5.bucket, events: ts5.events, series: ts5.series.map((s, i) => ({ name: s.name.replace("sem upstream", t("sem upstream")), data: s.data, color: ["#f87171", "#fb923c", "#fca5a5", "#ef4444", "#fdba74"][i % 5] })) });
    },
  };
}
function eventCard(x) {
  const e = x.event; const b = x.before; const d = x.during; const a = x.after;
  const bot = e.bot ? esc(e.bot) : "";
  const rows = [
    [t("Requisições totais"), "total"], [t("Páginas HTML"), "html"], ...(e.bot ? [[t("Requisições {b}", { b: bot }), "bot"], [t("{b} com 2xx (servido)", { b: bot }), "bot_2xx"]] : []),
    [t("403 por regra"), "s403_rule"], [t("403 sem regra"), "s403_other"], [t("2xx (todos)"), "s2xx"], ["5xx", "s5xx"],
  ];
  const fmt = (v) => (v == null ? "—" : v >= 100 ? n(v) : fnum(v));
  const chg = (k) => {
    const pb = b.per_hour[k]; const pa = a.per_hour[k];
    if (pb == null || pa == null) return `<span class="dim">${t("aguardando")}</span>`;
    if (!pb && !pa) return `<span class="dim">—</span>`;
    if (!pb) return `<span class="badge b-warn">${t("novo")}</span>`;
    const v = ((pa - pb) / pb) * 100;
    const bad = (k === "s5xx" || k === "s403_other") && v > 0;
    return `<span class="delta ${Math.abs(v) < 0.5 ? "" : bad ? "up" : "neutral-up"}">${v > 0 ? "▲" : "▼"} ${fnum(Math.abs(v), 0)}%</span>`;
  };
  const site = e.site != null ? siteName(e.site) : t("todos os proxy hosts");
  return `<section class="card ev-card"><h4>⏸ ${esc(e.title)}</h4>
    <div class="when">${fmtDT(e.ts, true)} (${esc(tzLabel(S.f.tz, e.ts * 1000))}) · ${esc(site)}</div>
    ${e.detail ? `<div class="detail">${esc(e.detail)}</div>` : ""}
    <table class="cmp"><thead><tr><th>${t("Média por hora")}</th><th>${t("Antes ({h} h)", { h: fmt(b.hours) })}</th><th>${t("Hora do evento")}</th><th>${t("Depois ({h} h)", { h: fmt(a.hours) })}</th><th>${t("Variação")}</th></tr></thead><tbody>
      ${rows.map(([l, k]) => `<tr><td>${l}</td><td>${fmt(b.per_hour[k])}</td><td class="dim">${n(d[k])}</td><td>${fmt(a.per_hour[k])}</td><td>${chg(k)}</td></tr>`).join("")}
    </tbody></table>
    ${x.bot_info ? `<div class="detail" style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)">
      ${t("<b>{b}</b>: última resposta servida (2xx) antes do evento na hora de <b>{h}</b>.", { b: bot, h: x.bot_info.last_2xx_hour_before ? fmtDT(x.bot_info.last_2xx_hour_before, true) : "—" })}
      ${t("A partir da hora do evento:")} ${x.bot_info.after_by_status.length ? x.bot_info.after_by_status.map((s) => `<span class="badge ${s.rule ? "b-rule" : s.status >= 500 ? "b-err" : s.status >= 400 ? "b-warn" : "b-ok"}">${s.status}${s.rule ? " " + t("regra") : ""}: ${n(s.hits)}</span>`).join(" ") : `<span class="dim">${t("nenhuma requisição")}</span>`}.</div>` : ""}
    ${a.hours < 1 ? `<div class="ignored">${t("Menos de 1 h completa após o evento — a média “depois” ainda é instável.")}</div>` : ""}</section>`;
}

/* ---- Páginas */
async function pagePages() {
  const pg = await api("pages", { q: S.ui.pq, sort: S.ui.psort, offset: S.ui.poff, limit: 50 });
  const html = `${head(t("Páginas mais solicitadas"), t("Caminhos sem query string, agrupados por host. Use os filtros do topo (domínio, período, status, bot, tipo) e a busca abaixo."))}
    ${caveat()}
    <section class="card"><div class="card-h"><div class="toolbar">
        <input class="search" id="pq" placeholder="${esc(t("Buscar caminho (ex.: /blog/, contato)…"))}" value="${esc(S.ui.pq)}">
        ${seg("psort", [["hits", t("Mais requisições")], ["bot", t("Mais bots")], ["s4xx", t("Mais 4xx")], ["s5xx", t("Mais 5xx")], ["bytes", t("Mais bytes")]], S.ui.psort)}
        ${seg("pkind", [["", t("Todos")], ["html", "HTML"], ["static", t("Estáticos")], ["api", "API"], ["other", t("Outros")]], S.f.kind)}
      </div><div class="dim small nowrap">${t("{n} requisições no filtro", { n: n(pg.total_hits) })}</div></div>
      <div class="card-b flush">${pagesTable(pg.rows, pg.total_hits, pg.offset)}${ignoredNote(pg.ignored)}</div>
      <div class="card-b toolbar" style="justify-content:space-between"><span class="dim small">${t("Linhas {a}–{b}", { a: pg.offset + 1, b: pg.offset + pg.rows.length })}</span>
        <div class="toolbar"><button class="btn ghost" id="pprev" ${pg.offset ? "" : "disabled"}>← ${t("anteriores")}</button><button class="btn ghost" id="pnext" ${pg.more ? "" : "disabled"}>${t("próximas")} →</button></div></div></section>`;
  return {
    html,
    mount(root) {
      let tm;
      $("#pq", root).oninput = (e) => { clearTimeout(tm); tm = setTimeout(() => { S.ui.pq = e.target.value.trim(); S.ui.poff = 0; render(true).then(() => { const i = $("#pq"); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }); }, 350); };
      bindSeg(root, "psort", (v) => { S.ui.psort = v; S.ui.poff = 0; render(true); });
      bindSeg(root, "pkind", (v) => { S.ui.poff = 0; go("pages", { kind: v }); });
      $("#pprev", root).onclick = () => { S.ui.poff = Math.max(0, S.ui.poff - 50); render(true); };
      $("#pnext", root).onclick = () => { S.ui.poff += 50; render(true); };
    },
  };
}
function pagesTable(rows, total, offset = 0, compact = false) {
  if (!rows.length) return `<div class="empty">${t("Nenhum caminho com esses filtros.")}</div>`;
  const max = Math.max(...rows.map((r) => r.hits));
  return `<div class="${compact ? "scroll short" : ""}" style="${compact ? "margin:0 -18px" : ""}"><table class="tbl"><thead><tr><th class="rank">#</th><th>${t("Caminho")}</th><th>${t("Tipo")}</th><th class="num">${t("Requisições")}</th><th class="num">% total</th><th class="num">% bots</th><th>Status</th>${compact ? "" : `<th class="num">Bytes</th>`}</tr></thead><tbody>
    ${rows.map((r, i) => `<tr><td class="rank">${offset + i + 1}</td><td><div class="path">${esc(r.path)}</div><div class="host">${esc(r.host)}</div></td><td>${kindBadge(r.kind)}</td>
      ${barCell(r.hits, max)}<td class="num dim">${pct(r.hits, total)}</td><td class="num">${pct(r.bot, r.hits)}</td><td style="width:120px">${statusStack(r)}</td>${compact ? "" : `<td class="num dim">${bytes(r.bytes)}</td>`}</tr>`).join("")}
    </tbody></table></div>`;
}

/* ---- Origem */
async function pageGeo() {
  const [g, refs] = await Promise.all([api("geo"), api("referrers")]);
  if (!S.worldLoaded) { echarts.registerMap("world", await (await fetch("vendor/world.json")).json()); S.worldLoaded = true; }
  const tot = sum(g.countries.map((c) => c.hits));
  const maxC = Math.max(...g.countries.map((c) => c.hits), 1);
  const pv = S.cfg.privacy;
  const html = `${head(t("Origem"), t("País e rede (ASN) vêm do GeoLite2 local, se configurado. IPs completos não são exibidos — só a rede /{a} (IPv4) ou /{b} (IPv6).", { a: pv.ipv4_prefix, b: pv.ipv6_prefix }))}
    ${siteHero()}
    ${notice(t("<b>IPs distintos é uma estimativa, não número de pessoas.</b> NAT, CGNAT de operadora, proxies, IPv6 rotativo e bots com centenas de IPs distorcem a contagem nos dois sentidos. Período: <b>{a}</b> IPs públicos distintos ({b} incluindo rede local).", { a: n(g.ips.public), b: n(g.ips.all) }), "warn")}
    <div class="grid g-21">
      ${card(t("Requisições por país"), t("Escala logarítmica"), `<div class="chart map" id="ch-map"></div>`)}
      ${card(t("Países"), "", `<div class="scroll" style="margin:0 -18px;max-height:470px"><table class="tbl"><thead><tr><th>${t("País")}</th><th class="num">${t("Requisições")}</th><th class="num">IPs <span class="est">≈</span></th><th class="num">% bots</th></tr></thead><tbody>
        ${g.countries.map((c) => `<tr><td class="nowrap"><span class="flag">${flag(c.cc)}</span>${esc(country(c.cc))}</td>${barCell(c.hits, maxC)}<td class="num">${n(c.ips)}</td><td class="num">${pct(c.bot, c.hits)}</td></tr>`).join("")}</tbody></table></div>${ignoredNote(g.ignored)}`)}
    </div>
    <div class="grid g-2 mt">
      ${card(t("Redes (ASN)"), t("Quem opera os IPs: nuvem, operadora, data center"), `<div class="scroll short" style="margin:0 -18px"><table class="tbl"><thead><tr><th>${t("Organização")}</th><th class="num">${t("Requisições")}</th><th class="num">IPs <span class="est">≈</span></th><th class="num">% bots</th></tr></thead><tbody>
        ${g.asns.map((r) => `<tr><td><b>${esc(r.org || (r.asn ? "AS" + r.asn : t("Rede local / desconhecida")))}</b><div class="host">${r.asn ? "AS" + r.asn : ""}</div></td>${barCell(r.hits, g.asns[0]?.hits)}<td class="num">${n(r.ips)}</td><td class="num">${pct(r.bot, r.hits)}</td></tr>`).join("")}</tbody></table></div>`)}
      ${card(t("Blocos de rede mais ativos"), t("Prefixo mascarado e classificação de UA dominante"), `<div class="scroll short" style="margin:0 -18px"><table class="tbl"><thead><tr><th>${t("Rede")}</th><th>${t("Organização")}</th><th class="num">${t("Requisições")}</th><th class="num">IPs</th><th>${t("UA dominante")}</th></tr></thead><tbody>
        ${g.networks.map((r) => `<tr><td class="nowrap mono"><span class="flag">${flag(r.cc)}</span>${esc(prefixLabel(r.prefix))}</td><td class="small muted">${esc(r.org || "—")}</td>${barCell(r.hits, g.networks[0]?.hits)}<td class="num">${n(r.ips)}</td>
          <td>${botBadge(r.top_ua_class)}</td></tr>`).join("")}</tbody></table></div>`)}
    </div>
    ${card(t("Referrers (sites de origem)"), t("Domínio do header Referer. “Seu” = domínio configurado no NPM (navegação interna)."), refs.rows.length ? `<div class="scroll short" style="margin:0 -18px"><table class="tbl"><thead><tr><th>${t("Domínio de origem")}</th><th class="num">${t("Requisições")}</th><th class="num">${t("Para HTML")}</th><th class="num">% bots</th></tr></thead><tbody>
      ${refs.rows.map((r) => `<tr><td><b>${esc(r.ref)}</b> ${r.internal ? `<span class="badge">${t("seu")}</span>` : ""}</td>${barCell(r.hits, refs.rows[0].hits, "rgba(34,211,238,.35)")}<td class="num">${n(r.html)}</td><td class="num">${pct(r.bot, r.hits)}</td></tr>`).join("")}
      </tbody></table></div>${ignoredNote(refs.ignored)}` : `<div class="empty">${t("Nenhum referrer no período.")}</div>`, "", "mt")}`;
  return {
    html,
    mount(root) {
      bindHero(root);
      const data = g.countries.filter((c) => c.cc).map((c) => ({ name: c.cc, value: Math.log10(c.hits + 1), raw: c }));
      const maxV = Math.max(...data.map((d) => d.value), 1);
      mk($("#ch-map", root), {
        tooltip: { ...TOOLTIP, trigger: "item", formatter: (p) => p.data ? `<b>${flag(p.name)} ${esc(country(p.name))}</b><br>${t("{n} requisições", { n: n(p.data.raw.hits) })} · ${pct(p.data.raw.hits, tot)}<br>${n(p.data.raw.ips)} IPs ≈ · ${pct(p.data.raw.bot, p.data.raw.hits)} bots` : `${esc(country(p.name))}<br><span style="color:#8b97ad">${t("sem requisições")}</span>` },
        visualMap: { min: 0, max: maxV, show: false, inRange: { color: ["#1e1b4b", "#3730a3", "#6366f1", "#818cf8", "#22d3ee"] } },
        series: [{ type: "map", map: "world", roam: true, nameProperty: "name", scaleLimit: { min: 1, max: 8 }, zoom: 1.15, top: 10, bottom: 10,
          itemStyle: { areaColor: "#141c2f", borderColor: "rgba(148,163,184,.18)", borderWidth: 0.6 },
          emphasis: { label: { show: false }, itemStyle: { areaColor: "#fbbf24" } }, select: { disabled: true }, data }],
      });
    },
  };
}

/* ---- Dados */
async function pageHealth() {
  const h = await apiRaw("health");
  const m = h.meta;
  const lag = S.meta.last_event_ts ? Date.now() / 1000 - S.meta.last_event_ts : null;
  const totalLines = sum(h.files.map((f) => f.lines));
  const bad = sum(h.files.map((f) => f.malformed));
  const rep = sum(h.files.map((f) => f.repaired));
  const groups = {};
  for (const f of h.files) (groups[f.base] ||= []).push(f);
  const fileRows = Object.entries(groups).sort((a, b) => sum(b[1].map((x) => x.lines)) - sum(a[1].map((x) => x.lines))).map(([base, fs]) =>
    fs.map((f, i) => {
      const active = f.name === base;
      const state = !f.exists ? `<span class="badge b-off">${t("expirado (removido)")}</span>` : f.done ? `<span class="badge b-ok">${t("lido")}</span>` : active ? `<span class="badge b-html"><i></i>${t("ativo")}</span>` : `<span class="badge b-warn">${t("parcial")}</span>`;
      const prog = f.done || !f.size ? 100 : Math.min(100, (100 * f.offset) / f.size);
      return `<tr><td>${i === 0 ? `<b>${esc(base)}</b>` : ""}</td><td class="mono small">${esc(f.name || "—")}</td><td>${state}</td><td><div class="progress" title="${n(f.offset)} / ${f.size != null ? n(f.size) : "?"} bytes"><i style="width:${prog}%"></i></div></td>
        <td class="num">${n(f.lines)}</td><td class="num">${f.malformed ? `<span class="badge b-warn">${n(f.malformed)}</span>` : "0"}</td><td class="num">${f.repaired ? n(f.repaired) : "0"}</td>
        <td class="nowrap small">${fmtDT(f.first_ts, true)}</td><td class="nowrap small">${fmtDT(f.last_ts, true)}</td><td class="mono small dim">${esc(f.fp)}</td></tr>`;
    }).join("")).join("");
  const offsets = (h.log_offsets || []).join(", ") || "—";
  const html = `${head(t("Dados & método"), t("Como os números são produzidos, o estado da coleta e a qualidade dos logs."), `${t("Fuso de exibição")}: ${esc(tzLabel())}`)}
    ${reindexWarning()}
    <div class="grid g-pill">
      ${pill({ label: t("Último ciclo de coleta"), value: m.last_cycle_end ? t("há {a}", { a: ago(+m.last_cycle_end) }) : "—", color: C.s2, sub: t("{s} s · {n} linhas novas", { s: m.last_cycle_secs || "—", n: n(+m.last_cycle_lines) }) })}
      ${pill({ label: t("Atraso do último evento"), value: lag != null ? ago(S.meta.last_event_ts) : "—", color: lag != null && lag < 900 ? C.s2 : C.s4, sub: fmtDT(S.meta.last_event_ts, true) })}
      ${pill({ label: t("Linhas agregadas"), value: nc(totalLines), color: C.html, sub: t("{n} arquivos rastreados", { n: h.files.length }) })}
      ${pill({ label: t("Linhas malformadas"), value: n(bad), color: bad ? C.s4 : C.s2, sub: t("não entram nas métricas") })}
      ${pill({ label: t("Linhas recuperadas"), value: n(rep), color: C.static, sub: t("precedidas de bytes nulos (queda de energia)") })}
      ${pill({ label: t("Banco de agregados"), value: bytes(h.db_size), color: "#94a3b8", sub: t("{n} linhas página×hora", { n: nc(h.counts.path_hits) }) })}
    </div>
    <div class="grid g-2 mt">
      ${card(t("Leitura incremental e rotação"), "", `<dl class="kv">
        <dt>${t("Configuração")}</dt><dd><code>${esc(h.config_path || t("(somente padrões/variáveis de ambiente)"))}</code></dd>
        <dt>${t("Identidade do arquivo")}</dt><dd>${t("fingerprint = sha1(base + 1ª linha). Acompanha a rotação <code>.log → .1.gz → .2.gz</code> sem reler.")}</dd>
        <dt>Offset</dt><dd>${t("bytes já lidos (descomprimidos), gravado na mesma transação dos agregados — queda no meio não duplica nem perde.")}</dd>
        <dt>${t("Rotação")}</dt><dd>${t("o resto do arquivo antigo é lido do <code>.1.gz</code> a partir do offset salvo.")}</dd>
        <dt>${t("Linha parcial")}</dt><dd>${t("só linhas terminadas em quebra de linha são lidas do arquivo ativo; o resto espera o próximo ciclo (a cada {s} s).", { s: h.interval })}</dd>
        <dt>${t("Malformadas")}</dt><dd>${t("contadas por arquivo e amostradas abaixo; bytes nulos antes de uma linha válida são removidos e a linha é aproveitada.")}</dd>
        <dt>${t("Agregação")}</dt><dd>${t("buckets por hora em UTC; dia/hora no fuso escolhido são montados na consulta (exato p/ fusos de hora cheia).")}</dd></dl>`)}
      ${card(t("Fusos e classificação"), "", `<dl class="kv">
        <dt>${t("Offsets no log")}</dt><dd><code>${esc(offsets)}</code> — ${t("o offset de cada linha é respeitado na conversão para UTC.")}</dd>
        <dt>User-Agent</dt><dd>${t("campo entre aspas logo após <code>[Sent-to …]</code> (formato <code>proxy</code>) ou após <code>[Gzip …]</code> (formato <code>standard</code>). O referrer é o campo seguinte e nunca é usado para classificar.")}</dd>
        <dt>HTML</dt><dd>${t("GET/HEAD para caminho sem extensão ou .html/.php/.asp. <b>Não</b> é pageview humano nem impressão de anúncio.")}</dd>
        <dt>${t("Estáticos")}</dt><dd>${t("js, css, imagens, fontes, mídia, /_next/static, /assets… (extensões extras em <code>classify.static_extensions_extra</code>)")}</dd>
        <dt>API</dt><dd>${t("/api/, hosts <code>api.*</code>, WebDAV/OCS, /wp-json e qualquer método de escrita (POST/PUT/…). Ajustável por site em <code>[sites.ID]</code>.")}</dd>
        <dt>${t("Outros")}</dt><dd>robots.txt, sitemap, ads.txt, .well-known, OPTIONS.</dd>
        <dt>${t("403 por regra")}</dt><dd>${t("status 403 + UA casando a regra <code>if ($http_user_agent ~* …) { return 403; }</code> do Advanced do proxy host, ou IP do bloco <code>geo</code> configurado. Lido do NPM em modo somente leitura.")}</dd>
        <dt>IPs</dt><dd>${t("guardados como HMAC (chave local) + rede mascarada; país/ASN via GeoLite2.")}</dd></dl>`)}
    </div>
    ${card(t("Regras de bloqueio detectadas no NPM"), t("Somente leitura — o painel nunca altera proxy hosts"), h.rules.length ? `<table class="tbl"><tbody>${h.rules.map((r) => `<tr><td><b>${esc(r.domains.join(", "))}</b> <span class="dim">#${r.site}</span></td><td>${r.rules.map((x) => `<span class="badge b-rule">UA ~* ${esc(x.pattern)} → ${x.status}</span>`).join(" ")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">${t("Nenhuma.")}</div>`, "", "mt")}
    ${card(t("Arquivos rastreados"), t("Cada linha é um arquivo físico identificado pelo fingerprint"), `<div class="scroll" style="margin:0 -18px"><table class="tbl"><thead><tr><th>Log</th><th>${t("Arquivo atual")}</th><th>${t("Estado")}</th><th>${t("Lido")}</th><th class="num">${t("Linhas")}</th><th class="num">${t("Malformadas")}</th><th class="num">${t("Recuperadas")}</th><th>${t("Primeiro evento")}</th><th>${t("Último evento")}</th><th>Fingerprint</th></tr></thead><tbody>${fileRows}</tbody></table></div>`, "", "mt")}
    ${card(t("Amostras de linhas malformadas"), t("Últimas 25"), h.malformed.length ? `<table class="tbl"><tbody>${h.malformed.map((x) => `<tr><td class="nowrap small">${fmtDT(x.seen)}</td><td class="small">${esc(x.file)}</td><td><span class="badge b-warn">${esc(x.reason)}</span></td><td class="ua">${esc(x.line)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">${t("Nenhuma linha malformada")} 👌</div>`, "", "mt")}
    <details class="rules card mt" style="padding:14px 18px"><summary>${t("Assinaturas de bots ({n} + genérica)", { n: h.bot_rules.length })}</summary>
      <table class="tbl mt"><tbody>${h.bot_rules.map((r) => `<tr><td class="nowrap"><b>${esc(botName(r.name))}</b></td><td class="dim small">${esc(groupName(r.group))}</td><td class="rx">${esc(r.regex)}</td></tr>`).join("")}
      ${h.generic_regex ? `<tr><td><b>${t("Outro bot (genérico)")}</b></td><td class="dim small">${t("Genérico")}</td><td class="rx">${esc(h.generic_regex)}</td></tr>` : ""}</tbody></table></details>`;
  return { html };
}

/* ------------------------------------------------------------------ boot */
async function boot() {
  try {
    S.cfg = await apiRaw("config");
    S.lang = S.cfg.language || "pt-BR";
    NF = new Intl.NumberFormat(S.lang);
    NC = new Intl.NumberFormat(S.lang, { notation: "compact", maximumFractionDigits: 1 });
    try { REGION = new Intl.DisplayNames([S.lang], { type: "region" }); } catch { REGION = null; }
    applyBranding();
    S.meta = await apiRaw("meta");
  } catch (e) {
    $("#app").innerHTML = `<div class="card"><div class="empty">${t("API indisponível:")} ${esc(e.message)}</div></div>`;
    return;
  }
  readHash();
  buildFilters();
  updateLive();
  window.addEventListener("hashchange", () => { S.ui.poff = 0; render(); });
  await render();
  if (S.cfg.refresh_seconds > 0) S.timer = setInterval(() => { if (!document.hidden) refresh(); }, S.cfg.refresh_seconds * 1000);
  setInterval(updateLive, 15000);
}
boot();
