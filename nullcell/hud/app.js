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
