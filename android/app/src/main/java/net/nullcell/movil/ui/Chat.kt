package net.nullcell.movil.ui

import androidx.compose.animation.AnimatedVisibility
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material.icons.filled.Forum
import androidx.compose.material.icons.filled.Group
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Tag
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import net.nullcell.movil.Canal
import net.nullcell.movil.Chat
import net.nullcell.movil.Estado
import net.nullcell.movil.MensajeChat
import net.nullcell.movil.Nodo
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Colores de apodo: el mismo apodo, el mismo color, en todas las pantallas. */
private val coloresApodo = listOf(
    Color(0xFF22D3EE), Color(0xFFFF4FD8), Color(0xFF34D399), Color(0xFFFBBF24),
    Color(0xFFA78BFA), Color(0xFFF472B6), Color(0xFF60A5FA), Color(0xFFFB923C),
)
private fun colorDe(apodo: String) = coloresApodo[Math.floorMod(apodo.lowercase().hashCode(), coloresApodo.size)]
private val hora = SimpleDateFormat("HH:mm", Locale.getDefault())

/**
 * El chat de la red, tipo IRC: un apodo que se recuerda, canales que se ven o
 * se escriben, y mensajes firmados que van por circuitos, sin servidor.
 */
@Composable
fun PantallaChat(e: Estado, avisar: (String) -> Unit) {
    val chat by Nodo.chat.collectAsStateWithLifecycle()
    val alcance = rememberCoroutineScope()
    var apodo by remember { mutableStateOf(Nodo.apodoGuardado) }
    var pedirApodo by remember { mutableStateOf(false) }
    var ocupado by remember { mutableStateOf(false) }

    // Mientras esta pestaña está a la vista, los mensajes se dan por leídos y
    // el chat se mira más seguido que el resto del estado.
    DisposableEffect(Unit) {
        Nodo.mirandoChat = true; Nodo.marcarLeido()
        onDispose { Nodo.mirandoChat = false }
    }
    LaunchedEffect(Unit) { while (true) { runCatching { Nodo.refrescarChat() }; delay(1500) } }

    val entrar: (String) -> Unit = { canal ->
        val a = apodo
        if (a == null) pedirApodo = true
        else if (!ocupado) {
            ocupado = true
            alcance.launch {
                Nodo.chatEntrar(canal, a).onSuccess(avisar).onFailure { avisar(it.message ?: "No se pudo entrar") }
                ocupado = false
            }
        }
    }
    val cambiarApodo: (String) -> Unit = { nuevo ->
        apodo = nuevo; Nodo.apodoGuardado = nuevo; pedirApodo = false
        // En un canal, el apodo nuevo se usa volviendo a entrar, como /nick.
        chat.canal?.let(entrar)
    }

    when {
        apodo == null -> PantallaApodo(inicial = "", primeraVez = true, guardar = cambiarApodo)
        chat.canal == null -> Vestibulo(apodo!!, e, ocupado, entrar) { pedirApodo = true }
        else -> Sala(chat, e, entrar, avisar, nick = cambiarApodo, cambiar = { pedirApodo = true }) {
            alcance.launch { Nodo.chatSalir(); avisar("Saliste del canal") }
        }
    }

    if (pedirApodo && apodo != null) {
        DialogoApodo(apodo!!, { pedirApodo = false }, cambiarApodo)
    }
}

// ------------------------------------------------------------------ apodo
@Composable
private fun PantallaApodo(inicial: String, primeraVez: Boolean, guardar: (String) -> Unit) {
    var texto by remember { mutableStateOf(inicial) }
    Column(
        Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            Modifier.size(84.dp).clip(CircleShape).background(C.cian.copy(alpha = .12f)).border(1.dp, C.cian.copy(alpha = .4f), CircleShape),
            contentAlignment = Alignment.Center,
        ) { Icon(Icons.Filled.Forum, null, tint = C.cian, modifier = Modifier.size(40.dp)) }
        Spacer(Modifier.height(18.dp))
        Text("Chat de la red", style = MaterialTheme.typography.titleLarge, color = C.texto)
        Spacer(Modifier.height(6.dp))
        Text(
            "Elige cómo te verán en los canales. Se recuerda en este teléfono y puedes cambiarlo cuando quieras.",
            style = MaterialTheme.typography.bodySmall, color = C.tenue,
        )
        Spacer(Modifier.height(20.dp))
        CampoApodo(texto, { texto = it }) { if (it.isNotBlank()) guardar(it.trim()) }
        Spacer(Modifier.height(14.dp))
        Button(
            onClick = { guardar(texto.trim()) }, enabled = texto.isNotBlank(), modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = C.cian, contentColor = C.fondo),
        ) { Text(if (primeraVez) "Empezar" else "Guardar") }
        Spacer(Modifier.height(14.dp))
        Text(
            "Si alguien ya usa ese apodo en el canal, entrarás como apodo2, apodo3… igual que en IRC.",
            style = MaterialTheme.typography.labelSmall, color = C.tenue,
        )
    }
}

@Composable
private fun CampoApodo(valor: String, cambiar: (String) -> Unit, listo: (String) -> Unit) =
    OutlinedTextField(
        valor, { cambiar(it.take(24)) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
        label = { Text("Apodo") }, leadingIcon = { Icon(Icons.Filled.Badge, null, tint = C.tenue) },
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { listo(valor) }),
        colors = campoColores(),
    )

@Composable
private fun DialogoApodo(actual: String, cerrar: () -> Unit, guardar: (String) -> Unit) {
    var texto by remember { mutableStateOf(actual) }
    AlertDialog(
        onDismissRequest = cerrar, containerColor = C.superficie2,
        title = { Text("Cambiar apodo") },
        text = {
            Column {
                CampoApodo(texto, { texto = it }) { if (it.isNotBlank()) guardar(it.trim()) }
                Spacer(Modifier.height(8.dp))
                Text("Si estás en un canal, vuelves a entrar con el nuevo.", style = MaterialTheme.typography.labelSmall, color = C.tenue)
            }
        },
        confirmButton = {
            Button(onClick = { guardar(texto.trim()) }, enabled = texto.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = C.cian, contentColor = C.fondo)) { Text("Guardar") }
        },
        dismissButton = { TextButton(onClick = cerrar) { Text("Cancelar") } },
    )
}

// ------------------------------------------------------------- vestíbulo
@Composable
private fun Vestibulo(apodo: String, e: Estado, ocupado: Boolean, entrar: (String) -> Unit, cambiarApodo: () -> Unit) {
    var escrito by remember { mutableStateOf("") }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Chat", style = MaterialTheme.typography.titleLarge, color = C.texto, modifier = Modifier.weight(1f))
                Row(
                    Modifier.clip(RoundedCornerShape(50)).clickable(onClick = cambiarApodo)
                        .background(colorDe(apodo).copy(alpha = .14f)).padding(horizontal = 12.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Filled.Badge, null, tint = colorDe(apodo), modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(apodo, color = colorDe(apodo), style = MaterialTheme.typography.labelLarge)
                }
            }
        }
        item {
            Tarjeta(acento = C.cian) {
                Text("Entrar a un canal", style = MaterialTheme.typography.titleMedium, color = C.texto)
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        escrito, { escrito = it.take(33) }, singleLine = true, modifier = Modifier.weight(1f),
                        placeholder = { Text("#general") },
                        leadingIcon = { Icon(Icons.Filled.Tag, null, tint = C.tenue) },
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go),
                        keyboardActions = KeyboardActions(onGo = { entrar(escrito.ifBlank { "general" }) }),
                        colors = campoColores(),
                    )
                    Spacer(Modifier.width(10.dp))
                    Button(
                        onClick = { entrar(escrito.ifBlank { "general" }) }, enabled = !ocupado,
                        colors = ButtonDefaults.buttonColors(containerColor = C.cian, contentColor = C.fondo),
                    ) {
                        if (ocupado) CircularProgressIndicator(Modifier.size(18.dp), color = C.fondo, strokeWidth = 2.dp)
                        else Text("Entrar")
                    }
                }
                if (!e.rutasListas) {
                    Spacer(Modifier.height(8.dp))
                    Text("Tu celda aún busca rutas: podrás entrar, y hablar en cuanto estén.", style = MaterialTheme.typography.labelSmall, color = C.ambar)
                }
            }
        }
        item { ListaCanales(ocupado = ocupado, alElegir = entrar) }
        item {
            Tarjeta(acento = C.magenta) {
                Text("Cómo funciona", style = MaterialTheme.typography.titleMedium, color = C.texto)
                Spacer(Modifier.height(8.dp))
                listOf(
                    "Sin servidor: cada mensaje va firmado y cifrado, de tu celda a la de cada persona del canal, por circuitos.",
                    "Como en IRC, no hay historial: ves lo que se dice mientras estás dentro.",
                    "Estar en un canal es público (cualquiera ve qué apodos hay); lo que se dice, no.",
                    "El chat usa una identidad aparte de la de tu celda.",
                ).forEach { Text("• $it", style = MaterialTheme.typography.bodySmall, color = C.tenue, modifier = Modifier.padding(vertical = 3.dp)) }
            }
        }
        item { Spacer(Modifier.height(60.dp)) }
    }
}

/** Los canales con gente dentro, del directorio de la red. */
@Composable
private fun ListaCanales(ocupado: Boolean, alElegir: (String) -> Unit) {
    var canales by remember { mutableStateOf<List<Canal>?>(null) }
    var cargando by remember { mutableStateOf(false) }
    val alcance = rememberCoroutineScope()
    val cargar: () -> Unit = {
        if (!cargando) { cargando = true; alcance.launch { canales = Nodo.chatCanales(); cargando = false } }
    }
    LaunchedEffect(Unit) { cargar() }

    Tarjeta(acento = C.verde, relleno = 6.dp) {
        Row(Modifier.padding(start = 10.dp, top = 6.dp, end = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Canales activos", style = MaterialTheme.typography.titleMedium, color = C.texto, modifier = Modifier.weight(1f))
            if (cargando) CircularProgressIndicator(Modifier.size(18.dp).padding(end = 4.dp), color = C.verde, strokeWidth = 2.dp)
            IconButton(onClick = cargar) { Icon(Icons.Filled.Refresh, "Actualizar", tint = C.tenue) }
        }
        val lista = canales.orEmpty()
        if (lista.isEmpty()) {
            Text(
                if (canales == null) "Buscando canales en la red…" else "No hay nadie en ningún canal ahora mismo. Abre uno: #general es el de siempre.",
                style = MaterialTheme.typography.bodySmall, color = C.tenue, modifier = Modifier.padding(10.dp),
            )
            if (canales != null) FilaCanal(Canal("general", 0, false), ocupado) { alElegir("general") }
        }
        lista.forEach { c -> FilaCanal(c, ocupado) { alElegir(c.nombre) } }
    }
}

@Composable
private fun FilaCanal(c: Canal, ocupado: Boolean, elegir: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(enabled = !ocupado && !c.dentro, onClick = elegir)
            .padding(horizontal = 10.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(34.dp).clip(RoundedCornerShape(10.dp)).background(colorDe(c.nombre).copy(alpha = .14f)),
            contentAlignment = Alignment.Center,
        ) { Text("#", color = colorDe(c.nombre), fontWeight = FontWeight.Bold) }
        Spacer(Modifier.width(12.dp))
        Text(c.nombre, style = MaterialTheme.typography.bodyLarge, color = C.texto, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (c.dentro) Pastilla("ESTÁS AQUÍ", C.verde)
        else Text(if (c.personas == 1) "1 persona" else "${c.personas} personas", style = MaterialTheme.typography.labelSmall, color = C.tenue)
    }
}

// ------------------------------------------------------------------ sala
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Sala(
    chat: Chat, e: Estado, entrar: (String) -> Unit, avisar: (String) -> Unit,
    nick: (String) -> Unit, cambiar: () -> Unit, salir: () -> Unit,
) {
    val alcance = rememberCoroutineScope()
    var texto by remember { mutableStateOf("") }
    var verPersonas by remember { mutableStateOf(false) }
    var verCanales by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val lista = rememberLazyListState()

    // Siempre al último mensaje cuando llega uno nuevo.
    LaunchedEffect(chat.mensajes.size) { if (chat.mensajes.isNotEmpty()) lista.animateScrollToItem(chat.mensajes.size - 1) }

    val enviar: () -> Unit = enviar@{
        val t = texto.trim()
        if (t.isEmpty()) return@enviar
        // Comandos de IRC, para quien los tenga en los dedos.
        when {
            t.startsWith("/join ", true) || t.startsWith("/j ", true) -> { entrar(t.substringAfter(' ').trim()); texto = "" }
            t.equals("/part", true) || t.equals("/leave", true) -> { salir(); texto = "" }
            t.startsWith("/nick ", true) -> {
                val n = t.substringAfter(' ').trim()
                if (n.isNotEmpty()) nick(n)
                texto = ""
            }
            t.startsWith("/") && !t.startsWith("//") -> avisar("Comandos: /join #canal · /nick apodo · /part")
            else -> {
                texto = ""
                alcance.launch { Nodo.chatDecir(t.removePrefix("/")).onFailure { avisar(it.message ?: "No se envió") } }
            }
        }
    }

    Column(Modifier.fillMaxSize().imePadding()) {
        // Cabecera del canal
        Row(
            Modifier.fillMaxWidth().background(C.superficie).padding(start = 16.dp, end = 4.dp, top = 10.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f).clickable { verCanales = true }) {
                Text("#${chat.canal}", style = MaterialTheme.typography.titleMedium, color = colorDe(chat.canal ?: ""), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    "${chat.miembros.size + 1} en el canal · ${chat.conectados} conectado${if (chat.conectados == 1) "" else "s"} contigo · tú: ${chat.apodo}",
                    style = MaterialTheme.typography.labelSmall, color = C.tenue, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            IconButton(onClick = { verPersonas = true }) { Icon(Icons.Filled.Group, "Personas", tint = C.texto) }
            IconButton(onClick = { verCanales = true }) { Icon(Icons.Filled.Tag, "Canales", tint = C.texto) }
            Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, "Más", tint = C.texto) }
                DropdownMenu(menu, { menu = false }, containerColor = C.superficie2) {
                    DropdownMenuItem({ Text("Cambiar apodo") }, { menu = false; cambiar() }, leadingIcon = { Icon(Icons.Filled.Badge, null) })
                    DropdownMenuItem({ Text("Salir del canal", color = C.rojo) }, { menu = false; salir() },
                        leadingIcon = { Icon(Icons.AutoMirrored.Filled.Logout, null, tint = C.rojo) })
                }
            }
        }
        AnimatedVisibility(!chat.listo) {
            Text(
                "Preparando el chat para que te alcancen… ${chat.porQueNo ?: ""}".trim(),
                style = MaterialTheme.typography.labelSmall, color = C.ambar,
                modifier = Modifier.fillMaxWidth().background(C.ambar.copy(alpha = .1f)).padding(horizontal = 16.dp, vertical = 8.dp),
            )
        }
        if (chat.pedido != null && chat.apodo != null && chat.pedido != chat.apodo) {
            Text(
                "«${chat.pedido}» ya estaba en uso en este canal: aquí eres ${chat.apodo}.",
                style = MaterialTheme.typography.labelSmall, color = C.cian,
                modifier = Modifier.fillMaxWidth().background(C.cian.copy(alpha = .08f)).padding(horizontal = 16.dp, vertical = 8.dp),
            )
        }

        // Mensajes
        LazyColumn(
            Modifier.weight(1f).fillMaxWidth(), state = lista,
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (chat.mensajes.isEmpty()) item {
                Text(
                    "Todavía no se ha dicho nada desde que entraste. Lo que escribas llega a quien esté en el canal ahora.",
                    style = MaterialTheme.typography.bodySmall, color = C.tenue, modifier = Modifier.padding(16.dp),
                )
            }
            items(chat.mensajes, key = { it.id }) { m -> Burbuja(m) }
        }

        // Escribir
        Row(
            Modifier.fillMaxWidth().background(C.superficie).padding(horizontal = 10.dp, vertical = 8.dp).navigationBarsPadding(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                texto, { texto = it.take(2000) }, modifier = Modifier.weight(1f), maxLines = 4,
                placeholder = { Text("Mensaje a #${chat.canal}") },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { enviar() }),
                shape = RoundedCornerShape(22.dp), colors = campoColores(),
            )
            Spacer(Modifier.width(8.dp))
            FilledIconButton(
                onClick = enviar, enabled = texto.isNotBlank(),
                colors = IconButtonDefaults.filledIconButtonColors(containerColor = C.cian, contentColor = C.fondo),
            ) { Icon(Icons.AutoMirrored.Filled.Send, "Enviar") }
        }
    }

    if (verPersonas) {
        ModalBottomSheet({ verPersonas = false }, containerColor = C.superficie2) {
            Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp)) {
                Text("En #${chat.canal}", style = MaterialTheme.typography.titleMedium, color = C.texto)
                Spacer(Modifier.height(12.dp))
                FilaPersona(chat.apodo ?: "tú", "tú", true)
                chat.miembros.forEach { p -> FilaPersona(p.apodo, corto(p.direccion), p.conectado) }
                Spacer(Modifier.height(10.dp))
                Text(
                    "Verde: tu celda tiene sesión abierta con esa persona y le llegan tus mensajes. Gris: aún conectando.",
                    style = MaterialTheme.typography.labelSmall, color = C.tenue,
                )
            }
        }
    }
    if (verCanales) {
        ModalBottomSheet({ verCanales = false }, containerColor = C.superficie2) {
            var escrito by remember { mutableStateOf("") }
            Column(Modifier.padding(horizontal = 16.dp).padding(bottom = 24.dp)) {
                Text("Cambiar de canal", style = MaterialTheme.typography.titleMedium, color = C.texto, modifier = Modifier.padding(start = 4.dp))
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    escrito, { escrito = it.take(33) }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text("Escribe un canal: #nombre") },
                    leadingIcon = { Icon(Icons.Filled.Tag, null, tint = C.tenue) },
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go),
                    keyboardActions = KeyboardActions(onGo = { if (escrito.isNotBlank()) { verCanales = false; entrar(escrito) } }),
                    colors = campoColores(),
                )
                Spacer(Modifier.height(10.dp))
                ListaCanales(ocupado = false) { verCanales = false; entrar(it) }
            }
        }
    }
}

@Composable
private fun FilaPersona(apodo: String, detalle: String, conectado: Boolean) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(36.dp).clip(CircleShape).background(colorDe(apodo).copy(alpha = .18f)), contentAlignment = Alignment.Center) {
            Text(apodo.take(1).uppercase(), color = colorDe(apodo), fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(apodo, color = C.texto, style = MaterialTheme.typography.bodyLarge)
            Text(detalle, color = C.tenue, style = MaterialTheme.typography.labelSmall)
        }
        Box(Modifier.size(10.dp).clip(CircleShape).background(if (conectado) C.verde else C.borde))
    }
}

@Composable
private fun Burbuja(m: MensajeChat) {
    val col = colorDe(m.apodo)
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (m.mio) Arrangement.End else Arrangement.Start) {
        Column(
            Modifier.widthIn(max = 300.dp)
                .clip(RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = if (m.mio) 16.dp else 4.dp, bottomEnd = if (m.mio) 4.dp else 16.dp))
                .background(if (m.mio) C.cian.copy(alpha = .16f) else C.superficie2)
                .border(1.dp, if (m.mio) C.cian.copy(alpha = .35f) else C.borde,
                    RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = if (m.mio) 16.dp else 4.dp, bottomEnd = if (m.mio) 4.dp else 16.dp))
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {
            if (!m.mio) Text(m.apodo, color = col, style = MaterialTheme.typography.labelLarge)
            Text(m.texto, color = C.texto, style = MaterialTheme.typography.bodyMedium)
            Text(
                hora.format(Date((m.ts * 1000).toLong())), color = C.tenue, style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.align(Alignment.End),
            )
        }
    }
}

@Composable
private fun campoColores() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = C.cian, unfocusedBorderColor = C.borde,
    focusedContainerColor = C.superficie2, unfocusedContainerColor = C.superficie2,
    cursorColor = C.cian,
)
