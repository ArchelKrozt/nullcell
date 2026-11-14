package net.nullcell.movil.ui

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ContentPaste
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.HourglassEmpty
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import net.nullcell.movil.Nodo
import net.nullcell.movil.Semilla
import net.nullcell.movil.Semillas

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PantallaSemillas(
    s: Semillas,
    avisar: (String) -> Unit,
    compartir: (String) -> Unit,
    prellenar: String?,
    alUsarPrellenado: () -> Unit,
) {
    val alcance = rememberCoroutineScope()
    var hoja by remember { mutableStateOf(false) }
    var aQuitar by remember { mutableStateOf<Semilla?>(null) }
    var intercambiando by remember { mutableStateOf(false) }
    if (prellenar != null && !hoja) hoja = true

    Box(Modifier.fillMaxSize()) {
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Text("Semillas", style = MaterialTheme.typography.titleLarge, color = C.texto, modifier = Modifier.padding(top = 8.dp))
                Text(
                    "Las puertas de entrada a la red. Las que contestan se comparten solas con los relevos, " +
                        "y así cada usuario nuevo recibe las que le faltan.",
                    style = MaterialTheme.typography.bodySmall, color = C.tenue,
                )
            }
            item {
                Fila {
                    Dato("Contestan", "${s.verificadas}/${s.total}", C.verde, Modifier.weight(1f))
                    Dato("De la red", s.nuevas.toString(), C.cian, Modifier.weight(1f), "recibidas")
                    Dato("Repartidas", s.compartidas.toString(), C.magenta, Modifier.weight(1f))
                }
            }
            item {
                Fila {
                    FilledTonalButton(
                        onClick = {
                            intercambiando = true
                            alcance.launch {
                                Nodo.intercambiar().fold({ avisar(it) }, { avisar("No se pudo: ${it.message}") })
                                intercambiando = false
                            }
                        },
                        enabled = !intercambiando,
                        modifier = Modifier.weight(1f),
                    ) {
                        if (intercambiando) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                        else Icon(Icons.Filled.SwapHoriz, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp)); Text(if (intercambiando) "Intercambiando…" else "Intercambiar ahora")
                    }
                    FilledTonalButton(
                        onClick = { (s.invitacion ?: s.propia)?.let(compartir) ?: avisar("Aún no hay invitación: espera a estar en la red") },
                        modifier = Modifier.weight(1f),
                    ) { Icon(Icons.Filled.Share, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Invitar") }
                }
            }
            if (s.lista.isEmpty()) {
                item {
                    Tarjeta { Text("Todavía no hay semillas. La del proyecto viene incluida: aparecerá en cuanto el nodo arranque.", color = C.tenue) }
                }
            }
            items(s.lista, key = { it.linea }) { semilla ->
                TarjetaSemilla(semilla, quitar = { aQuitar = semilla }, copiar = { compartir(semilla.linea) })
            }
            item { Spacer(Modifier.height(96.dp)) }
        }

        ExtendedFloatingActionButton(
            onClick = { hoja = true },
            icon = { Icon(Icons.Filled.Add, null) },
            text = { Text("Añadir semilla") },
            containerColor = C.cian, contentColor = C.fondo,
            modifier = Modifier.align(Alignment.BottomEnd).padding(20.dp),
        )
    }

    if (hoja) {
        val estado = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(
            onDismissRequest = { hoja = false; alUsarPrellenado() },
            sheetState = estado,
            containerColor = C.superficie,
            dragHandle = { Box(Modifier.padding(top = 10.dp).size(40.dp, 4.dp).clip(RoundedCornerShape(50)).background(C.borde)) },
        ) {
            HojaAgregar(
                inicial = prellenar.orEmpty(),
                cerrar = { hoja = false; alUsarPrellenado() },
                avisar = avisar,
            )
        }
    }

    aQuitar?.let { sem ->
        AlertDialog(
            onDismissRequest = { aQuitar = null },
            containerColor = C.superficie2,
            icon = { Icon(Icons.Filled.Delete, null, tint = C.rojo) },
            title = { Text("¿Quitar esta semilla?") },
            text = {
                Text(
                    "${sem.nota.ifBlank { corto(sem.nid) }} · ${sem.endpoint}\n\n" +
                        "Dejará de usarse para entrar y de compartirse. Las celdas que tu nodo ya conoce siguen en su tabla hasta que caduquen.",
                    color = C.tenue,
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        aQuitar = null
                        alcance.launch { Nodo.quitarSemilla(sem.linea).fold({ avisar(it) }, { avisar("No se pudo: ${it.message}") }) }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = C.rojo, contentColor = C.fondo),
                ) { Text("Quitar") }
            },
            dismissButton = { TextButton(onClick = { aQuitar = null }) { Text("Cancelar") } },
        )
    }
}

@Composable
private fun TarjetaSemilla(s: Semilla, quitar: () -> Unit, copiar: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    val (estadoTxt, estadoColor, estadoIcono) = when {
        s.verificada -> Triple("contesta · ${hace(s.ultimoOk)}", C.verde, Icons.Filled.CheckCircle)
        s.fallos > 0 -> Triple("no contesta · ${s.fallos} fallos", C.rojo, Icons.Filled.ErrorOutline)
        else -> Triple("sin comprobar", C.ambar, Icons.Filled.HourglassEmpty)
    }
    val origenColor = when (s.origen) { "tuya" -> C.magenta; "incluida" -> C.cian; else -> C.tenue }
    Tarjeta(Modifier.animateContentSize(), acento = estadoColor, relleno = 14.dp) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)).background(estadoColor.copy(alpha = .14f)),
                contentAlignment = Alignment.Center,
            ) { Icon(estadoIcono, null, tint = estadoColor, modifier = Modifier.size(22.dp)) }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    s.nota.ifBlank { "${corto(s.nid)}.null" },
                    style = MaterialTheme.typography.titleMedium, color = C.texto, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
                Text(s.endpoint, style = MaterialTheme.typography.labelSmall, color = C.tenue)
            }
            Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, "Opciones", tint = C.tenue) }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = C.superficie2) {
                    DropdownMenuItem(text = { Text("Compartir") }, leadingIcon = { Icon(Icons.Filled.Share, null) }, onClick = { menu = false; copiar() })
                    DropdownMenuItem(text = { Text("Quitar", color = C.rojo) }, leadingIcon = { Icon(Icons.Filled.Delete, null, tint = C.rojo) }, onClick = { menu = false; quitar() })
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Pastilla(s.origen.uppercase(), origenColor)
            Pastilla(estadoTxt.uppercase(), estadoColor)
            if (!s.publica) Pastilla("RED LOCAL", C.ambar)
        }
    }
}

@Composable
private fun HojaAgregar(inicial: String, cerrar: () -> Unit, avisar: (String) -> Unit) {
    val alcance = rememberCoroutineScope()
    val portapapeles = LocalClipboardManager.current
    var linea by remember { mutableStateOf(inicial) }
    var nota by remember { mutableStateOf("") }
    var enviando by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val colores = OutlinedTextFieldDefaults.colors(
        focusedBorderColor = C.cian, unfocusedBorderColor = C.borde, cursorColor = C.cian,
        focusedLabelColor = C.cian, unfocusedLabelColor = C.tenue,
    )
    Column(Modifier.fillMaxWidth().padding(20.dp).navigationBarsPadding().imePadding()) {
        Text("Añadir semilla", style = MaterialTheme.typography.titleLarge, color = C.texto)
        Spacer(Modifier.height(6.dp))
        Text(
            "Pega la línea de otra celda (dirección@ip:puerto) o una invitación nullcell://… " +
                "Se comprueba que conteste con su identidad antes de compartirla con nadie.",
            style = MaterialTheme.typography.bodySmall, color = C.tenue,
        )
        Spacer(Modifier.height(16.dp))
        OutlinedTextField(
            value = linea, onValueChange = { linea = it; error = null },
            label = { Text("Semilla o invitación") },
            placeholder = { Text("wuxq7z…@62.171.160.130:4433", color = C.tenue) },
            trailingIcon = {
                IconButton(onClick = { portapapeles.getText()?.text?.let { linea = it.trim(); error = null } }) {
                    Icon(Icons.Filled.ContentPaste, "Pegar", tint = C.cian)
                }
            },
            isError = error != null,
            supportingText = { error?.let { Text(it, color = C.rojo) } },
            colors = colores, modifier = Modifier.fillMaxWidth(), minLines = 2, maxLines = 4,
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = nota, onValueChange = { nota = it.take(80) },
            label = { Text("Nota (opcional)") }, placeholder = { Text("p. ej. Raspberry de casa", color = C.tenue) },
            singleLine = true, colors = colores, modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(20.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = cerrar, modifier = Modifier.weight(1f)) { Text("Cancelar") }
            Button(
                onClick = {
                    enviando = true
                    alcance.launch {
                        Nodo.agregarSemilla(linea, nota).fold(
                            { avisar(it); cerrar() },
                            { error = it.message },
                        )
                        enviando = false
                    }
                },
                enabled = linea.isNotBlank() && !enviando,
                colors = ButtonDefaults.buttonColors(containerColor = C.cian, contentColor = C.fondo),
                modifier = Modifier.weight(1f),
            ) {
                if (enviando) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = C.fondo)
                else Text("Añadir", fontWeight = FontWeight.SemiBold)
            }
        }
        Spacer(Modifier.height(12.dp))
    }
}

@Suppress("unused")
private fun Modifier.bordeSuave(c: Color) = this.border(1.dp, c, RoundedCornerShape(12.dp)).clickable { }
