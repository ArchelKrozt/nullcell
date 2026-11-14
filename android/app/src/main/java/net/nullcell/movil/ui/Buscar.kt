package net.nullcell.movil.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowForward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import net.nullcell.movil.Nodo
import net.nullcell.movil.Resultado

@Composable
fun PantallaBuscar(abrir: (String) -> Unit) {
    val alcance = rememberCoroutineScope()
    val foco = LocalFocusManager.current
    var q by rememberSaveable { mutableStateOf("") }
    var buscando by remember { mutableStateOf(false) }
    var resultados by remember { mutableStateOf<List<Resultado>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    fun lanzar() {
        val texto = q.trim()
        if (texto.isEmpty()) return
        foco.clearFocus()
        // Una dirección o un nombre .null se abre directamente.
        if (texto.endsWith(".null")) { abrir(texto); return }
        buscando = true; error = null
        alcance.launch {
            Nodo.buscar(texto).fold({ resultados = it }, { error = it.message })
            buscando = false
        }
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Spacer(Modifier.height(16.dp))
        Text("Buscar en la red", style = MaterialTheme.typography.titleLarge, color = C.texto)
        Text(
            "Por nombre, palabra o etiqueta. Las consultas viajan por circuitos: quien guarda el índice no sabe quién pregunta.",
            style = MaterialTheme.typography.bodySmall, color = C.tenue,
        )
        Spacer(Modifier.height(14.dp))
        OutlinedTextField(
            value = q, onValueChange = { q = it },
            placeholder = { Text("nullcell, documentación, p2p…", color = C.tenue) },
            leadingIcon = { Icon(Icons.Filled.Search, null, tint = C.cian) },
            trailingIcon = {
                Row {
                    if (q.isNotEmpty()) IconButton(onClick = { q = ""; resultados = null }) { Icon(Icons.Filled.Close, "Borrar", tint = C.tenue) }
                    IconButton(onClick = ::lanzar) { Icon(Icons.Filled.ArrowForward, "Buscar", tint = C.cian) }
                }
            },
            singleLine = true,
            shape = RoundedCornerShape(16.dp),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
            keyboardActions = KeyboardActions(onSearch = { lanzar() }),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = C.cian, unfocusedBorderColor = C.borde, cursorColor = C.cian,
                focusedContainerColor = C.superficie, unfocusedContainerColor = C.superficie,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        AnimatedContent(Triple(buscando, error, resultados), label = "resultados") { (cargando, err, lista) ->
            when {
                cargando -> Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        CircularProgressIndicator(color = C.cian)
                        Spacer(Modifier.height(12.dp))
                        Text("Preguntando a la red…", color = C.tenue)
                    }
                }
                err != null -> Tarjeta(acento = C.rojo) { Text(err, color = C.rojo) }
                lista == null -> Sugerencias { q = it; lanzar() }
                lista.isEmpty() -> Tarjeta { Text("Nada con «$q» todavía. Prueba otra palabra, o pide al autor que añada etiquetas a su portal.", color = C.tenue) }
                else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(bottom = 96.dp)) {
                    item { Text("${lista.size} resultado(s)", style = MaterialTheme.typography.labelSmall, color = C.tenue) }
                    items(lista, key = { it.nid }) { r -> TarjetaResultado(r) { abrir("${r.nid}.null") } }
                }
            }
        }
    }
}

@Composable
private fun TarjetaResultado(r: Resultado, abrir: () -> Unit) {
    Tarjeta(Modifier.clickable(onClick = abrir), acento = C.magenta) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)).background(C.magenta.copy(alpha = .15f)), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.Language, null, tint = C.magenta)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(r.titulo.ifBlank { corto(r.nid) }, style = MaterialTheme.typography.titleMedium, color = C.texto, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text("${corto(r.nid)}.null", style = MaterialTheme.typography.labelSmall, color = C.cian)
            }
            Icon(Icons.Filled.ArrowForward, null, tint = C.tenue)
        }
        if (r.desc.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text(r.desc, style = MaterialTheme.typography.bodyMedium, color = C.tenue, maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            r.tags.take(3).forEach { Pastilla("#$it", C.cian) }
        }
        if (r.porque.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            Text("Por qué aquí: ${r.porque}", style = MaterialTheme.typography.bodySmall, color = C.tenue)
        }
    }
}

@Composable
private fun Sugerencias(elegir: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Prueba con", style = MaterialTheme.typography.labelSmall, color = C.tenue)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("nullcell", "documentación", "p2p").forEach { s ->
                Box(Modifier.clip(RoundedCornerShape(50)).clickable { elegir(s) }) { Pastilla(s, C.magenta) }
            }
        }
    }
}
