package net.nullcell.movil.ui

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.net.Uri
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import net.nullcell.movil.Nodo
import java.io.ByteArrayInputStream

/** "nullcell", "nullcell.null", "https://x.null/a" → URL que carga el WebView. */
fun aUrl(destino: String): String {
    val d = destino.trim()
    if (d.startsWith("https://") || d.startsWith("http://")) return d.replaceFirst("http://", "https://")
    val host = if (d.contains(".null")) d else "$d.null"
    return "https://${host.trimEnd('/')}/"
}

@Composable
fun PantallaNavegador(
    destino: String?,
    anonimo: Boolean,
    compartir: (String) -> Unit,
    avisar: (String) -> Unit,
) {
    var web by remember { mutableStateOf<WebView?>(null) }
    var url by remember { mutableStateOf("") }
    var escrito by remember { mutableStateOf("") }
    var editando by remember { mutableStateOf(false) }
    var progreso by remember { mutableIntStateOf(0) }
    var puedeAtras by remember { mutableStateOf(false) }
    var puedeAdelante by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val portapapeles = LocalClipboardManager.current
    val foco = LocalFocusManager.current

    LaunchedEffect(destino, web) { if (destino != null) web?.loadUrl(aUrl(destino)) }
    BackHandler(enabled = puedeAtras) { web?.goBack() }

    Column(Modifier.fillMaxSize()) {
        // ------------------------------------------------ barra de dirección
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { web?.goBack() }, enabled = puedeAtras) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Atrás") }
            Row(
                Modifier.weight(1f).clip(RoundedCornerShape(50)).background(C.superficie2)
                    .clickable { editando = true; escrito = Uri.parse(url).host.orEmpty() }
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    if (anonimo) Icons.Filled.Lock else Icons.Filled.LockOpen, null,
                    tint = if (anonimo) C.verde else C.ambar, modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(8.dp))
                if (editando) {
                    BasicTextField(
                        value = escrito, onValueChange = { escrito = it }, singleLine = true,
                        textStyle = TextStyle(color = C.texto, fontSize = 14.sp, fontFamily = Mono),
                        cursorBrush = SolidColor(C.cian),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
                        keyboardActions = KeyboardActions(onGo = {
                            editando = false; foco.clearFocus()
                            if (escrito.isNotBlank()) web?.loadUrl(aUrl(escrito))
                        }),
                        modifier = Modifier.weight(1f),
                    )
                } else {
                    Text(
                        Uri.parse(url).host?.let { corto(it.removeSuffix(".null")) + ".null" } ?: "Escribe una dirección .null",
                        color = if (url.isBlank()) C.tenue else C.texto, fontFamily = Mono, fontSize = 14.sp,
                        maxLines = 1, modifier = Modifier.weight(1f),
                    )
                }
            }
            IconButton(onClick = { web?.reload() }) { Icon(Icons.Filled.Refresh, "Recargar") }
            Box {
                IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, "Más") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = C.superficie2) {
                    DropdownMenuItem(text = { Text("Adelante") }, enabled = puedeAdelante,
                        leadingIcon = { Icon(Icons.AutoMirrored.Filled.ArrowForward, null) }, onClick = { menu = false; web?.goForward() })
                    DropdownMenuItem(text = { Text("Ir a nullcell.null") }, leadingIcon = { Icon(Icons.Filled.Home, null) },
                        onClick = { menu = false; web?.loadUrl(aUrl("nullcell.null")) })
                    DropdownMenuItem(text = { Text("Copiar dirección") }, leadingIcon = { Icon(Icons.Filled.ContentCopy, null) },
                        onClick = { menu = false; portapapeles.setText(AnnotatedString(url)); avisar("Dirección copiada") })
                    DropdownMenuItem(text = { Text("Compartir") }, leadingIcon = { Icon(Icons.Filled.Share, null) },
                        onClick = { menu = false; if (url.isNotBlank()) compartir(url) })
                }
            }
        }
        if (progreso in 1..99) LinearProgressIndicator(progress = { progreso / 100f }, color = C.cian, trackColor = C.borde, modifier = Modifier.fillMaxWidth().height(2.dp))
        else Spacer(Modifier.height(2.dp))

        // ------------------------------------------------------- la página
        Box(Modifier.weight(1f).fillMaxWidth()) {
            AndroidView(
                factory = { ctx ->
                    WebView(ctx).also { w ->
                        configurar(w)
                        w.webViewClient = ClienteNull(
                            alCambiar = { u ->
                                url = u; puedeAtras = w.canGoBack(); puedeAdelante = w.canGoForward()
                            },
                        )
                        w.webChromeClient = object : WebChromeClient() {
                            override fun onProgressChanged(view: WebView, p: Int) { progreso = p }
                        }
                        web = w
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )
            if (url.isBlank() && destino == null) Bienvenida { web?.loadUrl(aUrl("nullcell.null")) }
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
private fun configurar(w: WebView) {
    w.setBackgroundColor(android.graphics.Color.parseColor("#07090D"))
    with(w.settings) {
        javaScriptEnabled = true          // muchos portales lo necesitan; cada uno en su propio origen
        domStorageEnabled = true
        allowFileAccess = false
        allowContentAccess = false
        @Suppress("DEPRECATION") saveFormData = false
        mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        cacheMode = WebSettings.LOAD_NO_CACHE
        setGeolocationEnabled(false)
        userAgentString = "Mozilla/5.0 (Nullcell; Android) Nullcell/0.1"   // igual para todos: no distingue a nadie
    }
    CookieManager.getInstance().setAcceptThirdPartyCookies(w, false)
}

/**
 * Todo lo que pide la página pasa por aquí. Lo `.null` lo trae el nodo por la
 * red; **lo demás se bloquea**: una imagen o una fuente de internet normal
 * saldría directa desde el teléfono y delataría tu IP al portal que la pusiera.
 */
private class ClienteNull(val alCambiar: (String) -> Unit) : WebViewClient() {
    override fun shouldInterceptRequest(view: WebView, req: WebResourceRequest): WebResourceResponse {
        val host = req.url.host.orEmpty()
        if (!host.endsWith(".null")) return bloqueada()
        if (req.method != "GET") return texto(405, "Este navegador todavía sólo hace peticiones GET.")
        return try {
            val p = Nodo.cargar(req.url.toString())
            val cab = mutableMapOf<String, String>()
            p.cabeceras.keys().forEach { k ->
                if (k.lowercase() !in setOf("content-length", "transfer-encoding", "connection", "content-encoding")) cab[k] = p.cabeceras.optString(k)
            }
            WebResourceResponse(p.tipo.ifBlank { "application/octet-stream" }, p.charset, p.status, razon(p.status), cab, ByteArrayInputStream(p.cuerpo))
        } catch (e: Throwable) {
            texto(502, "No se pudo cargar: ${e.message}")
        }
    }

    override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean =
        !req.url.host.orEmpty().endsWith(".null")   // enlaces a internet normal: no se siguen

    override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) = alCambiar(url)
    override fun doUpdateVisitedHistory(view: WebView, url: String, isReload: Boolean) = alCambiar(url)

    private fun bloqueada() = WebResourceResponse("text/plain", "utf-8", 403, "Forbidden", emptyMap(), ByteArrayInputStream(ByteArray(0)))
    private fun texto(status: Int, msg: String) = WebResourceResponse(
        "text/html", "utf-8", status, razon(status), emptyMap(),
        ByteArrayInputStream("<meta charset=utf-8><body style='background:#07090d;color:#e6edf3;font-family:sans-serif;padding:24px'>$msg".toByteArray()),
    )
    private fun razon(s: Int) = when (s) { 200 -> "OK"; 404 -> "Not Found"; 403 -> "Forbidden"; 405 -> "Method Not Allowed"; 502 -> "Bad Gateway"; 503 -> "Service Unavailable"; else -> "Status" }
}

@Composable
private fun Bienvenida(ir: () -> Unit) {
    Column(Modifier.fillMaxSize().background(C.fondo).padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Spacer(Modifier.height(60.dp))
        Box(Modifier.size(72.dp).clip(RoundedCornerShape(22.dp)).background(C.cian.copy(alpha = .14f)), contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.Public, null, tint = C.cian, modifier = Modifier.size(36.dp))
        }
        Spacer(Modifier.height(18.dp))
        Text("Navega la red Nullcell", style = MaterialTheme.typography.titleLarge, color = C.texto)
        Spacer(Modifier.height(8.dp))
        Text(
            "Escribe una dirección .null arriba, busca un portal, o empieza por el del proyecto.",
            style = MaterialTheme.typography.bodyMedium, color = C.tenue,
        )
        Spacer(Modifier.height(22.dp))
        Text(
            "Abrir nullcell.null",
            color = C.fondo, modifier = Modifier.clip(RoundedCornerShape(50)).background(C.cian).clickable(onClick = ir).padding(horizontal = 22.dp, vertical = 12.dp),
        )
    }
}
