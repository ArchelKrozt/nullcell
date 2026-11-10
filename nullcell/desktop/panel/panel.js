/* NULLCELL — el panel de la barra de menús (§9).
 *
 * Dos reglas gobiernan este fichero.
 *
 * 1. **La interfaz no inventa estado.** Un interruptor no se pinta activado
 *    porque lo hayas pulsado, sino cuando el nodo confirma que lo está. Es la
 *    diferencia entre informar y adivinar, y aquí adivinar significa decirle a
 *    alguien que está publicado cuando nadie puede alcanzarlo.
 *
 * 2. **"No se puede todavía" nunca es una respuesta completa.** Un botón
 *    apagado deja a la persona sin saber si esperar o actuar, que son
 *    reacciones opuestas. Un relevo *joven* se arregla esperando y hay cuenta
 *    atrás; una red con un solo operador no se arregla esperando nunca, y
 *    entonces lo que hace falta es una semilla.
 *
 * Las acciones que cambian algo van con el token en **cabecera**, nunca en
 * cookie ni en la URL: una página hostil puede hacer que el navegador adjunte
 * la cookie en un envío cross-site, pero no puede poner una cabecera.
 */
const $ = (id) => document.getElementById(id);

async function call(path, options = {}) {
  const response = await fetch(`${window.NCELL_API}${path}`, {
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

const post = (path, data) =>
  call(path, { method: "POST", body: JSON.stringify(data || {}) });

function note(id, text, bad = false) {
  const element = $(id);
  if (!text) { element.hidden = true; return; }
  element.textContent = text;
  element.classList.toggle("bad", bad);
  element.hidden = false;
}

function duracion(segundos) {
  if (segundos <= 0) return "ya";
  const m = Math.floor(segundos / 60), s = Math.round(segundos % 60);
  return m ? `${m} min ${s} s` : `${s} s`;
}

// --------------------------------------------------------------- pintar
function pintarRed(net, ready) {
  const { known = 0, usable = 0, needed = 3, operators = 0, ready_in: readyIn } = net || {};
  $("meter").hidden = ready;
  $("fill").style.width = `${Math.min(100, (usable / needed) * 100)}%`;

  if (ready) {
    $("hint").hidden = true;
    $("seedbox").hidden = true;
    return;
  }

  // Esperar y actuar son reacciones opuestas: hay que decir cuál toca.
  let hint;
  if (known === 0) {
    hint = "<strong>Esta celda no conoce ninguna otra.</strong> Pega la semilla de alguien " +
           "para entrar, o comparte la tuya y que entren a ti.";
  } else if (readyIn !== null && readyIn !== undefined) {
    hint = `<strong>Faltan ${duracion(readyIn)}.</strong> §7 pide ${needed} relevos de ` +
           `operadores distintos observados 10 min; llevas ${usable} de ${needed}. ` +
           "El reloj corre solo.";
  } else {
    hint = `<strong>${known} relevos conocidos en ${operators} operador` +
           `${operators === 1 ? "" : "es"}.</strong> §7 cuenta operadores, no máquinas: ` +
           `hacen falta ${needed} distintos, así que esperar no basta. Hace falta otra semilla.`;
  }
  $("hint").innerHTML = hint;
  $("hint").hidden = false;
  $("seedbox").hidden = false;
}

function pintarPortal(portal) {
  const box = $("portal");
  if (!portal) { box.hidden = true; $("publish").textContent = "Elegir…"; return; }
  box.hidden = false;
  $("publish").textContent = "Cambiar…";

  const live = portal.state === "en línea";
  const pill = $("portal-state");
  pill.textContent = live ? "en línea" : "sólo local";
  pill.classList.toggle("live", live);
  $("portal-folder").textContent = portal.folder;

  const preview = $("portal-preview");
  if (portal.preview) { preview.href = portal.preview; preview.hidden = false; }
  else preview.hidden = true;

  let detalle = live
    ? `Alcanzable en la red: ${portal.address}` +
      (portal.intro_points ? ` · ${portal.intro_points} puntos de enlace` : "")
    : "Se sirve en tu máquina y nadie más lo ve todavía. Sale a la red solo, " +
      "en cuanto haya relevos suficientes.";
  if (portal.warning) detalle += `\n⚠ ${portal.warning}`;
  note("portal-note", detalle, false);
}

async function refresh() {
  try {
    const [state, control] = await Promise.all([call("/api/state"), call("/api/control")]);
    const s = state.state || {};
    if (s.nid) {
      const corto = s.nid.length > 16 ? `${s.nid.slice(0, 6)}…${s.nid.slice(-6)}` : s.nid;
      $("addr").textContent = `${corto}.null`;
      $("addr").title = `${s.nid}.null`;
    }
    $("phase").textContent = control.paths_ready
      ? (s.phase || "listo")
      : (control.why_not_ready || "esperando relevos");
    $("dot").className = `dot ${control.paths_ready ? "up" : "wait"}`;
    $("stats").textContent = `${s.peers ?? 0} celdas · ${s.relays ?? 0} relevos usables`;

    // Arriba del todo y en magenta: si esto está puesto, se ve siempre.
    const danger = $("danger");
    danger.hidden = !control.anonymity_warning;
    danger.textContent = control.anonymity_warning || "";

    pintarRed(control.network, control.paths_ready);
    pintarPortal(control.portal);

    const share = $("share");
    if (!share.classList.contains("busy")) {
      share.setAttribute("aria-checked", String(Boolean(control.sharing)));
    }
    share.disabled = !control.can_share;
    if (!control.can_share) {
      // Decir "puedes compartir" cuando solo te alcanza tu propia maquina
      // publicaria un relevo inalcanzable en el directorio de todos.
      note("share-note", control.reach_detail + " Arráncalo con --announce y tu IP o " +
        "nombre público, con el puerto UDP abierto.", true);
    } else if (control.wants_sharing && !control.sharing) {
      note("share-note", "Pendiente: se activará en cuanto haya a quién anunciarlo.", false);
    } else if (control.reach === "lan") {
      note("share-note", control.reach_detail, false);
    } else {
      note("share-note", "", false);
    }
    // La invitación lleva varias semillas y el portal; la línea suelta es el
    // respaldo cuando todavía no hay nada más que ofrecer.
    const compartible = control.invitation || control.seed || "";
    $("copy-seed").hidden = !compartible;
    $("copy-seed").dataset.seed = compartible;
    $("copy-seed").textContent = control.invitation
      ? "Copiar mi invitación, para que otros entren"
      : "Copiar mi semilla, para que otros entren";

    // Publicar NO exige red: se sirve local y sale solo. El navegador sí.
    $("browse").disabled = !control.paths_ready;
  } catch (error) {
    $("phase").textContent = `sin contacto con el nodo: ${error.message}`;
    $("dot").className = "dot";
  }
}

// -------------------------------------------------------------- acciones
$("share").addEventListener("click", async () => {
  const button = $("share");
  if (button.disabled) return;
  const wanted = button.getAttribute("aria-checked") !== "true";
  button.classList.add("busy");
  try {
    const result = await post("/api/control/sharing", { on: wanted });
    button.setAttribute("aria-checked", String(Boolean(result.sharing)));
    note("share-note", result.detail || "", false);
  } catch (error) {
    note("share-note", error.message, true);
  } finally {
    button.classList.remove("busy");
    refresh();
  }
});

$("seed-add").addEventListener("click", async () => {
  const input = $("seed-input");
  const line = input.value.trim();
  if (!line) return;
  $("seed-add").disabled = true;
  try {
    const result = await post("/api/control/seed", { seed: line });
    const extra = result.portal ? ` · te invitan a ${result.portal}.null` : "";
    note("seed-note", result.detail + extra, !result.peers);
    if (result.peers) input.value = "";
  } catch (error) {
    note("seed-note", error.message, true);
  } finally {
    $("seed-add").disabled = false;
    refresh();
  }
});
$("seed-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("seed-add").click();
});

$("copy-seed").addEventListener("click", async (event) => {
  const seed = event.target.dataset.seed;
  try {
    await navigator.clipboard.writeText(seed);
    note("share-note", "Copiado. Dáselo a quien quieras que entre: no es un secreto ni avala " +
      "nada, sólo es una presentación (§7).", false);
  } catch {
    note("share-note", seed, false);  // sin portapapeles, al menos a la vista
  }
});

$("unpublish").addEventListener("click", async () => {
  try {
    const result = await call("/api/control/portal", { method: "DELETE" });
    note("portal-note", result.detail, false);
  } catch (error) {
    note("portal-note", error.message, true);
  }
  refresh();
});

$("publish").addEventListener("click", () => window.ncell.pickFolder());
$("browse").addEventListener("click", () => window.ncell.openBrowser());
$("quit").addEventListener("click", () => window.ncell.quit());

window.ncell = {
  pickFolder: () => { location.href = "ncell:pick-folder"; },
  openBrowser: () => { location.href = "ncell:open-browser"; },
  quit: () => { location.href = "ncell:quit"; },
};

window.ncellPublished = (result) => {
  if (result.error) { note("portal-note", result.error, true); return; }
  refresh();
};

refresh();
setInterval(refresh, 3000);
