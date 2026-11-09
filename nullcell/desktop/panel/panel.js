/* NULLCELL — el panel de la barra de menús (§9).
 *
 * Habla con la misma API local que el HUD, con una diferencia que importa: las
 * acciones que cambian algo van con el token en **cabecera**, nunca en cookie
 * ni en la URL. Una página hostil puede hacer que el navegador adjunte la
 * cookie en un envío cross-site; no puede poner una cabecera.
 *
 * Y la regla de §9 que manda aquí: la interfaz no inventa estado. Un switch no
 * se pinta como activado porque lo hayas pulsado, sino cuando el nodo confirma
 * que lo está. Mientras no contesta, se muestra ocupado.
 */
const $ = (id) => document.getElementById(id);
const api = (path) => `${window.NCELL_API}${path}`;

async function call(path, options = {}) {
  const response = await fetch(api(path), {
    ...options,
    headers: {
      "X-Nullcell-Token": window.NCELL_TOKEN,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function note(id, text, bad = false) {
  const element = $(id);
  if (!text) { element.hidden = true; return; }
  element.textContent = text;
  element.classList.toggle("bad", bad);
  element.hidden = false;
}

// ----------------------------------------------------------------- estado
async function refresh() {
  try {
    const [state, control] = await Promise.all([
      call("/api/state"),
      call("/api/control"),
    ]);
    paint(state.state || {}, control);
  } catch (error) {
    $("phase").textContent = `sin contacto con el nodo: ${error.message}`;
    $("dot").className = "dot";
  }
}

function paint(state, control) {
  if (state.nid) {
    // 56 caracteres de base32 no se leen, y el relleno del medio no dice nada.
    // El proyecto ya abrevia asi en todas partes (`identity.short`).
    const nid = state.nid;
    const corto = nid.length > 16 ? `${nid.slice(0, 6)}…${nid.slice(-6)}` : nid;
    const addr = $("addr");
    addr.textContent = `${corto}.null`;
    addr.title = `${nid}.null`;  // entera, al pasar el raton
  }
  $("phase").textContent = control.paths_ready
    ? state.phase || "listo"
    : control.why_not_ready || "esperando relevos";
  $("dot").className = `dot ${control.paths_ready ? "up" : "wait"}`;
  $("stats").textContent =
    `${state.peers ?? 0} celdas · ${state.relays ?? 0} relevos usables`;

  const share = $("share");
  if (!share.classList.contains("busy")) {
    share.setAttribute("aria-checked", String(Boolean(control.sharing)));
  }
  share.disabled = !control.can_share;
  if (!control.can_share) {
    note("share-note",
      "Esta celda no sabe por dónde es alcanzable, así que no puede ofrecerse como " +
      "relevo. Hace falta arrancarla con una dirección pública (--announce).", true);
  }

  $("publish").disabled = !control.paths_ready;
  $("browse").disabled = !control.paths_ready;
}

// ---------------------------------------------------------------- acciones
$("share").addEventListener("click", async () => {
  const button = $("share");
  if (button.disabled) return;
  const wanted = button.getAttribute("aria-checked") !== "true";
  button.classList.add("busy");
  try {
    const result = await call("/api/control/sharing", {
      method: "POST",
      body: JSON.stringify({ on: wanted }),
    });
    // Lo que diga el nodo, no lo que pulsó la persona.
    button.setAttribute("aria-checked", String(Boolean(result.sharing)));
    note("share-note", result.detail || "", false);
  } catch (error) {
    note("share-note", error.message, true);
  } finally {
    button.classList.remove("busy");
    refresh();
  }
});

$("publish").addEventListener("click", () => window.ncell.pickFolder());
$("browse").addEventListener("click", () => window.ncell.openBrowser());
$("quit").addEventListener("click", () => window.ncell.quit());

// El shell nos avisa del resultado de un diálogo nativo.
window.ncellPublished = (result) => {
  if (result.error) { note("publish-note", result.error, true); return; }
  note("publish-note",
    `Publicada ${result.folder}\nen https://${result.address}` +
    (result.warning ? `\n⚠ ${result.warning}` : ""));
};

refresh();
setInterval(refresh, 3000);
