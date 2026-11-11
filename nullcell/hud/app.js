/* NULLCELL HUD — §9, §16.
 *
 * Plain JS, no build step, no dependencies. Live data arrives over a
 * WebSocket, never by polling. Two limits come from §16: the console is a ring
 * buffer capped at CONSOLE_MAX rows, and the seals are fetched once as a
 * sprite and referenced with <use> rather than redrawn per render.
 *
 * Nothing here invents a number. Every figure on screen comes from the state
 * the node sent, and a portal is described as "visto hace 4 m · 7 celdas",
 * never as a percentage of uptime nobody can verify (§9).
 */
"use strict";

const CONSOLE_MAX = 500;
const FRESH_SECONDS = 3600;

const el = (id) => document.getElementById(id);
const dom = {
  nid: el("nid"),
  dht: el("dht-mode"),
  count: el("circuits-count"),
  circuits: el("circuits"),
  node: el("node"),
  portals: el("portals"),
  topology: el("topology"),
  console: el("console"),
  link: el("link"),
  sigils: el("sigils"),
  form: el("search-form"),
  query: el("q"),
  progress: el("search-progress"),
  resultsPanel: el("panel-results"),
  results: el("results"),
};

let sigilsLoaded = new Set();

function short(nid) {
  return nid && nid.length > 10 ? nid.slice(0, 6) + "…" + nid.slice(-2) : nid || "—";
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString("es", { hour12: false });
}

function ago(seconds) {
  if (seconds == null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} m`;
  return `${Math.round(seconds / 3600)} h`;
}

function span(cls, text) {
  const node = document.createElement("span");
  node.className = cls;
  node.textContent = text;
  return node;
}

/* ------------------------------------------------------------------- seals */
async function loadSigils(nids) {
  // Fetched once per new set, not redrawn per frame (§16). The sprite is
  // generated on this machine from sha256(nid): no request leaves here.
  const missing = nids.filter((nid) => nid && !sigilsLoaded.has(nid));
  if (!missing.length) return;
  try {
    const response = await fetch("/api/sigils.svg", { credentials: "same-origin" });
    dom.sigils.innerHTML = await response.text();
    nids.forEach((nid) => sigilsLoaded.add(nid));
  } catch (error) {
    /* a missing seal is cosmetic; the panel still works */
  }
}

function seal(nid) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "seal");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#sigil-${nid}`);
  svg.append(use);
  return svg;
}

/* ---------------------------------------------------------------- circuits */
function renderCircuits(circuits) {
  dom.count.textContent = `${circuits.length} circuito${circuits.length === 1 ? "" : "s"}`;
  if (!circuits.length) {
    dom.circuits.innerHTML =
      '<p class="waiting">ningún circuito todavía — se necesitan 3 relevos en prefijos distintos (§7)</p>';
    return;
  }
  dom.circuits.replaceChildren(...circuits.map(renderCircuit));
}

function renderCircuit(circuit) {
  const node = document.createElement("div");
  node.className = "circuit";
  node.dataset.state = circuit.state || "up";

  const head = document.createElement("div");
  head.className = "head";
  head.append(
    span("id", "C" + (circuit.id || 0).toString(16).padStart(8, "0")),
    span("dim", `${(circuit.hops || []).length} saltos`),
    span("dim", `${circuit.frames_sent || 0}↑ ${circuit.frames_received || 0}↓`),
    span("rtt", circuit.rtt_ms != null ? `${circuit.rtt_ms.toFixed(0)} ms` : "—"),
  );

  const hops = document.createElement("div");
  hops.className = "hops";
  (circuit.hops || []).forEach((hop, index) => {
    if (index) hops.append(span("wire", "───"));
    const box = document.createElement("span");
    box.className = "hop";
    box.append(span("dot", "●"), document.createTextNode(" " + short(hop)));
    hops.append(box);
  });

  node.append(head, hops);
  return node;
}

/* -------------------------------------------------------------------- node */
function renderNode(state) {
  dom.nid.textContent = state.nid || "—";
  dom.nid.title = state.nid || "";
  dom.dht.textContent = `dht: ${state.anonymous_dht ? "por circuitos" : "directo"}`;
  dom.dht.dataset.state = state.anonymous_dht ? "ok" : "warn";
  dom.dht.title = state.anonymous_dht
    ? "§7 decisión 8: las consultas no revelan tu dirección"
    : "las consultas van por enlace directo y revelan tu dirección (§7)";

  const rows = [
    ["estado", state.phase || "listo"],
    ["celdas conocidas", state.peers ?? "—"],
    ["relevos usables", state.relays ?? "—"],
    ["guards fijados", (state.guards || []).map(short).join(" ") || "ninguno"],
    ["registros en el DHT", state.records ?? "—"],
    ["circuitos servidos", state.legs ?? "—"],
    ["observaciones firmadas", state.observations ?? "—"],
  ];
  dom.node.replaceChildren(
    ...rows.flatMap(([key, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = String(value);
      return [dt, dd];
    }),
  );
}

/* ----------------------------------------------------------------- portals */
function renderPortals(portals) {
  if (!portals || !portals.length) {
    dom.portals.innerHTML =
      '<p class="waiting">ningún portal conocido todavía — el gossip los rellena en minutos (§9)</p>';
    return;
  }
  dom.portals.replaceChildren(
    ...portals.map((portal) => {
      const card = document.createElement("div");
      card.className = "card";
      card.dataset.stale = String((portal.age ?? 0) > FRESH_SECONDS);
      card.append(seal(portal.nid));
      card.append(span("name", portal.title || short(portal.nid)));
      // §9: what we know, not a percentage nobody can verify. When signed
      // observations exist their wording wins, because that is evidence from
      // other cells rather than a note we wrote to ourselves.
      card.append(
        span(
          "meta",
          `${portal.kind || "web"} · ~${ago(portal.age)} · ${portal.confirmed || 1}✓`,
        ),
      );
      card.title = portal.seen ? `${portal.nid}\n${portal.seen}` : portal.nid;
      return card;
    }),
  );
}

/* ---------------------------------------------------------------- topology */
function renderTopology(topology) {
  if (!topology || !topology.cells) {
    dom.topology.innerHTML = '<p class="waiting">sin tabla de rutas todavía</p>';
    return;
  }
  const bars = document.createElement("div");
  bars.className = "bars";
  const peak = Math.max(1, ...topology.buckets.map(([, count]) => count));
  for (const [index, count] of topology.buckets) {
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${Math.round((count / peak) * 100)}%`;
    bar.title = `bucket ${index}: ${count} celdas (k=${topology.k})`;
    bars.append(bar);
  }
  const caption = span(
    "dim small",
    `${topology.cells} celdas en ${topology.buckets_in_use} buckets · ` +
      "los buckets cercanos quedan vacíos hasta que la red es enorme (§7)",
  );
  dom.topology.replaceChildren(bars, caption);
}

/* ------------------------------------------------------------------ search */
dom.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = dom.query.value.trim();
  if (!query) {
    dom.resultsPanel.hidden = true;
    dom.progress.textContent = "";
    return;
  }
  dom.progress.textContent = "buscando…";
  dom.resultsPanel.hidden = false;
  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`, {
      credentials: "same-origin",
    });
    const payload = await response.json();
    dom.progress.textContent = payload.progress || "";
    renderResults(payload.results || []);
  } catch (error) {
    dom.progress.textContent = "la búsqueda falló";
  }
});

/* Una búsqueda puede venir en la URL (`/?q=pan`), que es como el navegador de
 * §9 entrega lo que se escribe en su barra: su barra de direcciones entiende
 * direcciones .null y, cuando lo tecleado no lo es, lo trata como búsqueda. El
 * token se quita de la URL al entrar, así que esto corre después. */
(function buscarDesdeLaURL() {
  const pedida = new URLSearchParams(location.search).get("q");
  if (!pedida) return;
  dom.query.value = pedida;
  dom.form.requestSubmit();
})();

function renderResults(results) {
  if (!results.length) {
    dom.results.innerHTML = '<p class="waiting">sin resultados</p>';
    return;
  }
  dom.results.replaceChildren(
    ...results.map((result) => {
      const node = document.createElement("div");
      node.className = "result";
      const head = document.createElement("div");
      head.className = "head";
      head.append(
        span("title", result.title || short(result.nid)),
        span("kind", result.kind || "web"),
        span("score", String(result.score)),
      );
      node.append(head);
      if (result.desc) node.append(span("why", result.desc));
      // §9: the ranking explains itself instead of showing one opaque number.
      node.append(span("why", result.why || ""));
      node.append(span("addr", result.nid + ".null"));
      return node;
    }),
  );
}

/* ----------------------------------------------------------------- console */
function appendEvents(events) {
  const atBottom =
    dom.console.scrollHeight - dom.console.scrollTop - dom.console.clientHeight < 24;

  for (const event of events) {
    const line = document.createElement("div");
    line.className = "line";
    line.dataset.level = event.level || "info";
    line.append(span("t", clock(event.ts)), span("k", event.kind));
    line.append(document.createTextNode(event.text));
    if (event.count > 1) line.append(span("n", ` ×${event.count}`));
    dom.console.append(line);
  }

  // Ring buffer: drop from the front so the DOM stays bounded (§16).
  while (dom.console.childElementCount > CONSOLE_MAX) {
    dom.console.firstElementChild.remove();
  }
  if (atBottom) dom.console.scrollTop = dom.console.scrollHeight;
}

/* -------------------------------------------------------------- transport */
function connect() {
  const socket = new WebSocket(`ws://${location.host}/ws`);

  socket.onopen = () => {
    dom.link.textContent = "conectado";
    dom.link.className = "";
  };
  socket.onclose = () => {
    dom.link.textContent = "desconectado — reintentando";
    dom.link.className = "dim";
    setTimeout(connect, 1500);
  };
  socket.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === "state") {
      const state = payload.state || {};
      renderNode(state);
      renderCircuits(state.circuits || []);
      renderPortals(state.portals || []);
      renderTopology(state.topology);
      const nids = [
        ...(state.portals || []).map((p) => p.nid),
        ...(state.circuits || []).flatMap((c) => c.hops || []),
      ];
      loadSigils(nids);
    } else if (payload.type === "events") {
      appendEvents(payload.events || []);
    }
  };
}

connect();

/* ------------------------------------------------------------- chat (M10)
 *
 * Tipo IRC: entras a un nombre, ves quién está y lo que se dice mientras
 * estás. Nada se guarda en disco, ni aquí ni en el nodo: lo que no estabas
 * para leer, no se lee. Guardarlo significaría que *alguien* lo guarda.
 *
 * El texto se inserta con textContent y nunca como HTML. Un mensaje de chat
 * es lo más parecido a entrada hostil que recibe esta página: lo escribe
 * cualquiera del canal y lo pinta tu navegador.
 */
const chat = {
  join: el("chat-join"),
  say: el("chat-say"),
  channel: el("chat-channel"),
  nick: el("chat-nick"),
  text: el("chat-text"),
  log: el("chat-log"),
  status: el("chat-status"),
  seen: new Set(),
};

async function chatCall(path, body) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    credentials: "same-origin",
    headers: {
      // En cabecera y no sólo en la cookie: lo que cambia algo exige el token
      // donde una página de otro origen no puede ponerlo. Sin esto, la
      // casilla de semillas y el chat contestaban 403 desde el propio
      // tablero, que es el único sitio desde el que se usan.
      "X-Nullcell-Token": window.NCELL_TOKEN || "",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function pintarChat(state) {
  if (!state || !state.channel) {
    chat.status.textContent = "sin canal";
    chat.say.hidden = true;
    return;
  }
  chat.say.hidden = false;
  if (!chat.channel.value.trim() || chat.channel.value === "#general") {
    chat.channel.value = `#${state.channel}`;
  }
  if (!chat.nick.value.trim()) chat.nick.value = state.nick;
  const conectados = state.connected || 0;
  const miembros = (state.members || []).length;
  chat.status.textContent = state.ready
    ? `#${state.channel} · ${state.nick} · ${conectados} de ${miembros} conectados`
    : `#${state.channel} · ${state.why_not_ready || "preparando"}`;

  for (const m of state.messages || []) {
    if (chat.seen.has(m.mid)) continue;
    chat.seen.add(m.mid);
    const line = document.createElement("div");
    line.className = m.mine ? "chat-line mine" : "chat-line";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = `${m.nick}:`;          // nunca innerHTML
    const what = document.createElement("span");
    what.className = "what";
    what.textContent = m.text;               // idem
    line.append(who, what);
    chat.log.append(line);
    while (chat.log.childElementCount > CONSOLE_MAX) chat.log.firstElementChild.remove();
  }
  chat.log.scrollTop = chat.log.scrollHeight;
}

chat.join.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    chat.seen.clear();
    chat.log.replaceChildren();
    pintarChat(await chatCall("/api/chat/join", {
      channel: chat.channel.value, nick: chat.nick.value,
    }));
  } catch (error) {
    chat.status.textContent = error.message;
  }
});

chat.say.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = chat.text.value.trim();
  if (!text) return;
  chat.text.value = "";
  try {
    pintarChat(await chatCall("/api/chat/say", { text }));
  } catch (error) {
    chat.status.textContent = error.message;
  }
});

/* El estado del chat vive en el nodo, no en esta pestaña. Sin esto, recargar
 * la página mostraba «sin canal» mientras el nodo seguía dentro: la interfaz
 * contando su propia versión de los hechos, que es justo lo que §9 prohíbe. */
async function refrescarChat() {
  try {
    pintarChat(await chatCall("/api/chat"));
  } catch { /* el nodo contestará en el siguiente ciclo */ }
}

refrescarChat();
setInterval(refrescarChat, 2000);

/* ------------------------------------------- el estado de la red (M10)
 *
 * El tablero decía «1 relevos conocidos, 0 usables» y ahí se acababa la
 * explicación: ni cuál es cuál, ni qué le pasa a cada uno, ni qué hacer. Esto
 * contesta tres preguntas en orden —¿puedo navegar?, ¿qué falta?, ¿quién
 * hay?— y deja añadir una semilla sin salir de aquí.
 *
 * Todo el texto se inserta con textContent: una dirección y un extremo los
 * escribe otra gente, y esta página los pinta.
 */
const red = {
  verdict: el("verdict"),
  steps: el("steps"),
  cells: el("cells"),
  form: el("seed-form"),
  input: el("seed-input"),
  note: el("seed-note"),
};

function paso(estado, titulo, texto) {
  const box = document.createElement("div");
  box.className = `step ${estado}`;
  const b = document.createElement("b");
  b.textContent = titulo;
  box.append(b, document.createTextNode(texto));
  return box;
}

/* La cuenta atrás corre en la página, no en el servidor: pedirla cada cuatro
 * segundos daría un número que salta, y «usable en 10 min» no se movía nunca
 * y parecía colgado. Se guarda el instante en que vence y se repinta cada
 * segundo desde ahí. */
let vence = new Map();
let venceRed = null;

function cuentaAtras(segundos) {
  if (segundos <= 0) return "ya";
  const m = Math.floor(segundos / 60);
  const s = Math.floor(segundos % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function tic() {
  const ahora = Date.now();
  for (const [nodo, cuando] of vence) {
    if (!nodo.isConnected) { vence.delete(nodo); continue; }
    const quedan = Math.max(0, (cuando - ahora) / 1000);
    nodo.textContent = quedan > 0 ? `usable en ${cuentaAtras(quedan)}` : "ya cumplió, comprobando…";
  }
  const total = el("countdown");
  if (total && venceRed !== null) {
    const quedan = Math.max(0, (venceRed - ahora) / 1000);
    total.textContent = quedan > 0 ? ` — faltan ${cuentaAtras(quedan)}` : "";
  }
}
setInterval(tic, 1000);

function pintarRed(control) {
  const net = control.network || {};
  const cells = control.cells || [];
  const conocidas = cells.length;
  const usables = cells.filter((c) => c.state === "usable").length;
  const esperando = cells.filter((c) => c.state === "esperando").length;
  const operadores = net.operators ?? 0;
  const hacenFalta = net.needed ?? 3;

  // 1. ¿Puedo navegar?
  if (control.paths_ready) {
    red.verdict.className = "verdict ok";
    red.verdict.textContent = control.anonymous
      ? "Puedes navegar y publicar. La red funciona."
      : "Puedes navegar y publicar — pero SIN ANONIMATO (circuitos cortos).";
  } else if (conocidas === 0) {
    red.verdict.className = "verdict no";
    red.verdict.textContent =
      "No conoces ninguna celda todavía. Pega abajo la invitación de alguien para entrar.";
  } else if (operadores < hacenFalta) {
    red.verdict.className = "verdict no";
    red.verdict.textContent =
      `Esperar no basta: conoces ${operadores} operador${operadores === 1 ? "" : "es"} ` +
      `y hacen falta ${hacenFalta}. Necesitas la semilla de otra máquina, de otro sitio.`;
  } else {
    red.verdict.className = "verdict wait";
    red.verdict.replaceChildren(
      document.createTextNode(
        `Ya sólo hay que esperar: ${esperando} celda${esperando === 1 ? "" : "s"} ` +
          "cumpliendo sus 10 minutos de antigüedad",
      ),
      Object.assign(document.createElement("span"), { id: "countdown" }),
    );
    const falta = net.ready_in;
    venceRed = falta === null || falta === undefined ? null : Date.now() + falta * 1000;
  }
  if (control.paths_ready || conocidas === 0 || operadores < hacenFalta) venceRed = null;

  // 2. ¿Qué falta, en orden?
  red.steps.replaceChildren(
    paso(
      conocidas ? "done" : "todo",
      `1 · Entrar ${conocidas ? "✓" : ""}`,
      conocidas ? `conoces ${conocidas} celda${conocidas === 1 ? "" : "s"}` : "pega una invitación",
    ),
    paso(
      operadores >= hacenFalta ? "done" : "todo",
      `2 · Operadores ${operadores >= hacenFalta ? "✓" : ""}`,
      `${operadores} de ${hacenFalta}. Cuentan operadores distintos, no máquinas`,
    ),
    paso(
      usables >= hacenFalta ? "done" : esperando ? "wait" : "todo",
      `3 · Antigüedad ${usables >= hacenFalta ? "✓" : ""}`,
      `${usables} usable${usables === 1 ? "" : "s"}; 10 min observada cada una`,
    ),
  );

  // 3. ¿Quién hay?
  red.cells.replaceChildren();
  vence = new Map();
  if (!cells.length) return;
  for (const c of cells) {
    const row = document.createElement("div");
    row.className = `cell ${c.state}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = c.short;
    if (c.guard) {
      const g = document.createElement("span");
      g.className = "guard";
      g.textContent = "  · guard";
      who.append(g);
    }
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = c.state;
    const why = document.createElement("span");
    why.className = "why";
    const donde = document.createTextNode(`${c.endpoint || "sin dirección"} — `);
    const que = document.createElement("span");
    if (c.state === "esperando") {
      vence.set(que, Date.now() + (c.usable_in || 0) * 1000);
      que.textContent = `usable en ${cuentaAtras(c.usable_in || 0)}`;
    } else {
      que.textContent = c.detail;
    }
    why.append(donde, que);
    row.append(who, tag, why);
    red.cells.append(row);
  }
  tic(); // sin esperar al primer segundo: si no, el reloj nace vacío
}

red.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const line = red.input.value.trim();
  if (!line) return;
  try {
    const result = await chatCall("/api/control/seed", { seed: line });
    red.note.className = result.peers ? "note" : "note bad";
    red.note.textContent =
      result.detail + (result.portal ? ` · te invitan a ${result.portal}.null` : "");
    red.note.hidden = false;
    if (result.peers) red.input.value = "";
  } catch (error) {
    red.note.className = "note bad";
    red.note.textContent = error.message;
    red.note.hidden = false;
  }
  refrescarRed();
});

async function refrescarRed() {
  try {
    pintarRed(await chatCall("/api/control"));
  } catch {
    red.verdict.className = "verdict no";
    red.verdict.textContent = "este nodo no expone controles (sólo lectura)";
  }
}

refrescarRed();
setInterval(refrescarRed, 4000);
