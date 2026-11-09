/* NULLCELL HUD — §9, §16.
 *
 * Plain JS, no build step, no dependencies. Live data arrives over a
 * WebSocket, never by polling, and the console is a ring buffer capped at
 * CONSOLE_MAX rows: without a cap the DOM grows without limit over a long
 * session and the browser eventually dies (§16).
 */
"use strict";

const CONSOLE_MAX = 500;

const el = {
  nid: document.getElementById("nid"),
  phase: document.getElementById("phase"),
  count: document.getElementById("circuits-count"),
  circuits: document.getElementById("circuits"),
  node: document.getElementById("node"),
  console: document.getElementById("console"),
  link: document.getElementById("link"),
};

function short(nid) {
  return nid && nid.length > 10 ? nid.slice(0, 6) + "…" + nid.slice(-2) : nid || "—";
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString("es", { hour12: false });
}

/* ---------------------------------------------------------------- circuits */
function renderCircuits(circuits) {
  el.count.textContent = `${circuits.length} circuito${circuits.length === 1 ? "" : "s"}`;
  if (!circuits.length) {
    el.circuits.innerHTML =
      '<p class="waiting">ningún circuito todavía — se necesitan 3 relevos en prefijos distintos (§7)</p>';
    return;
  }
  el.circuits.replaceChildren(...circuits.map(renderCircuit));
}

function renderCircuit(circuit) {
  const node = document.createElement("div");
  node.className = "circuit";
  node.dataset.state = circuit.state || "up";

  const head = document.createElement("div");
  head.className = "head";
  head.append(
    span("id", "C" + (circuit.id || "").toString(16).padStart(8, "0")),
    span("dim", `${circuit.hops ? circuit.hops.length : 0} saltos`),
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

function span(cls, text) {
  const node = document.createElement("span");
  node.className = cls;
  node.textContent = text;
  return node;
}

/* -------------------------------------------------------------------- node */
function renderNode(state) {
  el.nid.textContent = state.nid || "—";
  el.nid.title = state.nid || "";
  el.phase.textContent = state.phase || "listo";
  el.phase.dataset.state = state.phase === "listo" ? "ok" : "warn";

  const rows = [
    ["dirección", state.nid ? short(state.nid) : "—"],
    ["celdas conocidas", state.peers != null ? state.peers : "—"],
    ["relevos usables", state.relays != null ? state.relays : "—"],
    ["guards fijados", (state.guards || []).map(short).join(" ") || "ninguno"],
    ["registros en el DHT", state.records != null ? state.records : "—"],
    ["circuitos servidos", state.legs != null ? state.legs : "—"],
  ];
  el.node.replaceChildren(
    ...rows.flatMap(([key, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = String(value);
      return [dt, dd];
    }),
  );
}

/* ----------------------------------------------------------------- console */
function appendEvents(events) {
  const atBottom =
    el.console.scrollHeight - el.console.scrollTop - el.console.clientHeight < 24;

  for (const event of events) {
    const line = document.createElement("div");
    line.className = "line";
    line.dataset.level = event.level || "info";
    line.append(span("t", clock(event.ts)), span("k", event.kind));
    line.append(document.createTextNode(event.text));
    if (event.count > 1) line.append(span("n", ` ×${event.count}`));
    el.console.append(line);
  }

  // Ring buffer: drop from the front so the DOM stays bounded (§16).
  while (el.console.childElementCount > CONSOLE_MAX) {
    el.console.firstElementChild.remove();
  }
  if (atBottom) el.console.scrollTop = el.console.scrollHeight;
}

/* -------------------------------------------------------------- transport */
function connect() {
  const socket = new WebSocket(`ws://${location.host}/ws`);

  socket.onopen = () => {
    el.link.textContent = "conectado";
    el.link.className = "";
  };
  socket.onclose = () => {
    el.link.textContent = "desconectado — reintentando";
    el.link.className = "dim";
    setTimeout(connect, 1500);
  };
  socket.onmessage = (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === "state") {
      renderNode(payload.state);
      renderCircuits(payload.state.circuits || []);
    } else if (payload.type === "events") {
      appendEvents(payload.events || []);
    }
  };
}

connect();
