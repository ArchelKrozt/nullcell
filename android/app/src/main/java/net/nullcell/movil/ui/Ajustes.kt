package net.nullcell.movil.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import net.nullcell.movil.Estado

@Composable
fun PantallaAjustes(e: Estado, avisar: (String) -> Unit, salir: () -> Unit) {
    val portapapeles = LocalClipboardManager.current
    var confirmarSalir by remember { mutableStateOf(false) }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Ajustes", style = MaterialTheme.typography.titleLarge, color = C.texto, modifier = Modifier.padding(top = 8.dp)) }
        item {
            Opcion(Icons.Filled.Fingerprint, "Tu celda", "${corto(e.nid)}.null · la dirección es tu clave pública", C.cian) {
                e.nid?.let { portapapeles.setText(AnnotatedString("$it.null")); avisar("Dirección copiada") }
            }
        }
        item {
            Tarjeta(acento = if (e.anonimo) C.verde else C.ambar) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Shield, null, tint = if (e.anonimo) C.verde else C.ambar)
                    Spacer(Modifier.width(12.dp))
                    Text("Anonimato automático", style = MaterialTheme.typography.titleMedium, color = C.texto, modifier = Modifier.weight(1f))
                    Candado(e.anonimo)
                }
                Spacer(Modifier.height(10.dp))
                Text(
                    "La conexión va siempre cifrada de extremo a extremo. Con menos de 3 operadores distintos en la red " +
                        "los circuitos son cortos y no hay anonimato; en cuanto hay 3, la app pasa sola a 3 saltos. " +
                        "Ahora: ${if (e.saltos > 0) "${e.saltos} salto(s)" else "esperando rutas"} · ${e.operadores} operador(es).",
                    style = MaterialTheme.typography.bodySmall, color = C.tenue,
                )
            }
        }
        item {
            Tarjeta(acento = C.magenta) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Lock, null, tint = C.magenta); Spacer(Modifier.width(12.dp))
                    Text("Qué protege este navegador", style = MaterialTheme.typography.titleMedium, color = C.texto)
                }
                Spacer(Modifier.height(10.dp))
                listOf(
                    "Sólo carga direcciones .null: nada sale a internet normal, así que ningún portal puede descubrir tu IP con una imagen o un script.",
                    "Sin caché, sin cookies de terceros, sin guardar formularios.",
                    "Tu identidad vive en el almacenamiento privado de la app y nunca se copia a la nube.",
                    "Este teléfono no presta ancho de banda ni se anuncia: sólo navega y comparte semillas.",
                ).forEach { Text("• $it", style = MaterialTheme.typography.bodySmall, color = C.tenue, modifier = Modifier.padding(vertical = 3.dp)) }
            }
        }
        item { TarjetaActualizaciones() }
        item {
            Opcion(Icons.Filled.Info, "Acerca de", "Nullcell ${net.nullcell.movil.Actualizador.versionActual} · AGPL-3.0 · github.com/ArchelKrozt/nullcell", C.tenue) {
                portapapeles.setText(AnnotatedString("https://github.com/ArchelKrozt/nullcell")); avisar("Enlace copiado")
            }
        }
        item { Opcion(Icons.Filled.PowerSettingsNew, "Desconectar y salir", "Cierra los circuitos y detiene el nodo", C.rojo) { confirmarSalir = true } }
    }
    if (confirmarSalir) {
        AlertDialog(
            onDismissRequest = { confirmarSalir = false }, containerColor = C.superficie2,
            title = { Text("¿Desconectar?") },
            text = { Text("Se cierran los circuitos y la app deja de estar en la red hasta que la vuelvas a abrir.", color = C.tenue) },
            confirmButton = { Button(onClick = { confirmarSalir = false; salir() }, colors = ButtonDefaults.buttonColors(containerColor = C.rojo, contentColor = C.fondo)) { Text("Desconectar") } },
            dismissButton = { TextButton(onClick = { confirmarSalir = false }) { Text("Cancelar") } },
        )
    }
}

@Composable
private fun Opcion(icono: ImageVector, titulo: String, sub: String, color: Color, accion: () -> Unit) {
    Tarjeta(Modifier.fillMaxWidth().clickable(onClick = accion), acento = color) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icono, null, tint = color, modifier = Modifier.size(22.dp))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(titulo, style = MaterialTheme.typography.titleMedium, color = C.texto)
                Text(sub, style = MaterialTheme.typography.bodySmall, color = C.tenue)
            }
            if (titulo == "Tu celda") Icon(Icons.Filled.ContentCopy, null, tint = C.tenue, modifier = Modifier.size(18.dp))
        }
    }
}
