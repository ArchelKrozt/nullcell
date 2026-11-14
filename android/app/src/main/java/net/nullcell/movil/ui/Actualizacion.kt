package net.nullcell.movil.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import net.nullcell.movil.Act
import net.nullcell.movil.Actualizador
import net.nullcell.movil.Version

private fun mb(b: Long) = "%.1f MB".format(b / 1_048_576.0)

/** Notas de la release sin el marcado de Markdown más ruidoso. */
private fun notas(v: Version) = v.notas
    .lines().filterNot { it.trim().startsWith("```") }
    .joinToString("\n") { it.replace(Regex("^#+\\s*"), "").replace("**", "").replace("`", "") }
    .trim().ifEmpty { "Sin notas." }

/**
 * Aviso flotante, encima de cualquier pestaña: hay versión nueva → descargar
 * (con progreso) → instalar. Si el usuario lo deja para luego no vuelve a
 * salir en esta sesión; sigue disponible en Ajustes.
 */
@Composable
fun DialogoActualizacion() {
    val ctx = LocalContext.current
    val est by Actualizador.estado.collectAsStateWithLifecycle()
    val pospuesta by Actualizador.pospuesta.collectAsStateWithLifecycle()

    // En cuanto la descarga termina y está verificada, se abre el instalador.
    LaunchedEffect(est) {
        val e = est
        if (e is Act.Lista && Actualizador.puedeInstalar(ctx)) Actualizador.instalar(ctx, e.apk)
    }

    val v = when (val e = est) {
        is Act.Disponible -> e.v
        is Act.Descargando -> e.v
        is Act.Lista -> e.v
        is Act.Error -> e.v
        else -> null
    } ?: return
    if (pospuesta == v.nombre) return

    AlertDialog(
        onDismissRequest = { if (est !is Act.Descargando) Actualizador.posponer(v) },
        containerColor = C.superficie2,
        icon = { Icon(Icons.Filled.SystemUpdate, null, tint = C.cian) },
        title = { Text("Nullcell ${v.nombre} disponible") },
        text = {
            Column {
                Text("Tienes la ${Actualizador.versionActual}. Descarga de ${mb(v.bytes)} desde GitHub.", color = C.tenue, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(10.dp))
                when (val e = est) {
                    is Act.Descargando -> {
                        LinearProgressIndicator(progress = { e.progreso }, modifier = Modifier.fillMaxWidth(), color = C.cian, trackColor = C.borde)
                        Spacer(Modifier.height(6.dp))
                        Text("Descargando… ${(e.progreso * 100).toInt()} %", color = C.texto, style = MaterialTheme.typography.labelSmall)
                    }
                    is Act.Lista -> Text(
                        if (Actualizador.puedeInstalar(ctx)) "Descargada y verificada (SHA-256 y firma). Confirma la instalación en la ventana de Android."
                        else "Descargada y verificada. Android pide permitir que Nullcell instale actualizaciones: actívalo y vuelve aquí.",
                        color = C.verde, style = MaterialTheme.typography.bodySmall,
                    )
                    is Act.Error -> Text(e.mensaje, color = C.rojo, style = MaterialTheme.typography.bodySmall)
                    else -> Text(
                        notas(v), color = C.texto, style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.heightIn(max = 220.dp).verticalScroll(rememberScrollState()),
                    )
                }
            }
        },
        confirmButton = {
            when (val e = est) {
                is Act.Descargando -> {}
                is Act.Lista -> Button(
                    onClick = { Actualizador.instalar(ctx, e.apk) },
                    colors = ButtonDefaults.buttonColors(containerColor = C.verde, contentColor = C.fondo),
                ) { Text(if (Actualizador.puedeInstalar(ctx)) "Instalar" else "Dar permiso") }
                else -> Button(
                    onClick = { Actualizador.descargar(v) },
                    colors = ButtonDefaults.buttonColors(containerColor = C.cian, contentColor = C.fondo),
                ) { Text(if (est is Act.Error) "Reintentar" else "Actualizar") }
            }
        },
        dismissButton = {
            if (est !is Act.Descargando) TextButton(onClick = { Actualizador.posponer(v) }) { Text("Más tarde") }
        },
    )
}

/** Tarjeta de Ajustes: versión instalada, buscar ahora y el interruptor automático. */
@Composable
fun TarjetaActualizaciones() {
    val ctx = LocalContext.current
    val est by Actualizador.estado.collectAsStateWithLifecycle()
    var auto by remember { mutableStateOf(Actualizador.automatico) }
    Tarjeta(acento = C.cian) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Filled.SystemUpdate, null, tint = C.cian)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text("Actualizaciones", style = MaterialTheme.typography.titleMedium, color = C.texto)
                Text("Instalada: ${Actualizador.versionActual}", style = MaterialTheme.typography.bodySmall, color = C.tenue)
            }
            if (est is Act.Buscando) CircularProgressIndicator(Modifier.size(20.dp), color = C.cian, strokeWidth = 2.dp)
        }
        Spacer(Modifier.height(10.dp))
        val (txt, col) = when (val e = est) {
            Act.Nada -> "Toca «Buscar ahora» para comprobar." to C.tenue
            Act.Buscando -> "Preguntando a GitHub…" to C.tenue
            Act.AlDia -> "Tienes la última versión." to C.verde
            is Act.Disponible -> "Hay una nueva: ${e.v.nombre}." to C.ambar
            is Act.Descargando -> "Descargando ${e.v.nombre}… ${(e.progreso * 100).toInt()} %" to C.cian
            is Act.Lista -> "${e.v.nombre} descargada y verificada, lista para instalar." to C.verde
            is Act.Error -> e.mensaje to C.rojo
        }
        Text(txt, style = MaterialTheme.typography.bodySmall, color = col)
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            when (val e = est) {
                is Act.Disponible -> Button(onClick = { Actualizador.descargar(e.v) }, colors = ButtonDefaults.buttonColors(containerColor = C.cian, contentColor = C.fondo)) { Text("Actualizar") }
                is Act.Lista -> Button(onClick = { Actualizador.instalar(ctx, e.apk) }, colors = ButtonDefaults.buttonColors(containerColor = C.verde, contentColor = C.fondo)) { Text("Instalar") }
                is Act.Descargando, Act.Buscando -> {}
                else -> OutlinedButton(onClick = { Actualizador.buscar() }) { Text("Buscar ahora", color = C.cian) }
            }
            Spacer(Modifier.weight(1f))
            Text("Automático", style = MaterialTheme.typography.labelSmall, color = C.tenue)
            Spacer(Modifier.width(8.dp))
            Switch(
                checked = auto,
                onCheckedChange = { auto = it; Actualizador.automatico = it },
                colors = SwitchDefaults.colors(checkedThumbColor = C.fondo, checkedTrackColor = C.cian),
            )
        }
        Spacer(Modifier.height(6.dp))
        Text(
            "Es lo único de la app que sale a internet normal: se pregunta a GitHub (que ve tu IP) como mucho cada 6 h. " +
                "El APK se comprueba antes de instalar: huella SHA-256 publicada y misma firma que esta app.",
            style = MaterialTheme.typography.labelSmall, color = C.tenue,
        )
    }
}
