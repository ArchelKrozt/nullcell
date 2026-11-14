package net.nullcell.movil

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Una versión publicada en GitHub (`android-vX.Y.Z`). */
data class Version(
    val nombre: String, val notas: String, val url: String,
    val bytes: Long, val sha256: String?, val pagina: String,
)

sealed interface Act {
    data object Nada : Act
    data object Buscando : Act
    data object AlDia : Act
    data class Disponible(val v: Version) : Act
    data class Descargando(val v: Version, val progreso: Float) : Act
    data class Lista(val v: Version, val apk: File) : Act
    data class Error(val mensaje: String, val v: Version? = null) : Act
}

/**
 * Busca versiones nuevas de la app en las *releases* de GitHub, las descarga,
 * comprueba que son de verdad (SHA-256 publicado por GitHub y **misma firma**
 * que la app instalada) y abre el instalador de Android.
 *
 * Esto es lo único de la app que sale a internet normal, fuera de la red
 * Nullcell: GitHub ve la IP del teléfono cuando pregunta. Por eso la
 * comprobación automática se puede apagar en Ajustes.
 */
object Actualizador {
    private const val TAG = "Nullcell"
    private const val API = "https://api.github.com/repos/ArchelKrozt/nullcell/releases?per_page=20"
    private const val PREFIJO = "android-v"
    private const val CADA_MS = 6 * 3600 * 1000L

    private lateinit var app: Context
    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var tarea: Job? = null
    private val _estado = MutableStateFlow<Act>(Act.Nada)
    val estado: StateFlow<Act> = _estado.asStateFlow()

    /** La versión que ya avisó y el usuario dejó para luego (no se insiste en esta sesión). */
    private val _pospuesta = MutableStateFlow<String?>(null)
    val pospuesta: StateFlow<String?> = _pospuesta.asStateFlow()

    private val prefs get() = app.getSharedPreferences("actualizaciones", Context.MODE_PRIVATE)

    fun init(ctx: Context) { app = ctx.applicationContext }

    val versionActual: String by lazy { info(app.packageName)?.versionName ?: "?" }

    var automatico: Boolean
        get() = prefs.getBoolean("automatico", true)
        set(v) { prefs.edit().putBoolean("automatico", v).apply() }

    /** Al abrir la app: pregunta como mucho cada 6 h, y sólo si está activado. */
    fun alArrancar() {
        if (!automatico) return
        if (System.currentTimeMillis() - prefs.getLong("ultima", 0) < CADA_MS) {
            // Si en la última consulta había una nueva, se vuelve a avisar sin preguntar.
            prefs.getString("pendiente", null)?.let { buscar() }
            return
        }
        buscar()
    }

    fun buscar() {
        if (tarea?.isActive == true) return
        _estado.value = Act.Buscando
        tarea = alcance.launch {
            _estado.value = try {
                val v = ultima()
                prefs.edit().putLong("ultima", System.currentTimeMillis())
                    .putString("pendiente", v?.nombre).apply()
                if (v == null) Act.AlDia else Act.Disponible(v)
            } catch (e: Exception) {
                Log.w(TAG, "buscar actualización", e)
                Act.Error("No se pudo consultar GitHub: ${e.message ?: e.javaClass.simpleName}")
            }
        }
    }

    fun posponer(v: Version) { _pospuesta.value = v.nombre }

    /** La release `android-v*` más nueva que la instalada, con su APK; o null. */
    private fun ultima(): Version? {
        val c = (URL(API).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000; readTimeout = 20_000
            setRequestProperty("Accept", "application/vnd.github+json")
            setRequestProperty("User-Agent", "nullcell-android")
        }
        val texto = try {
            if (c.responseCode != 200) throw IllegalStateException("GitHub respondió ${c.responseCode}")
            c.inputStream.bufferedReader().readText()
        } finally { c.disconnect() }
        val lista = JSONArray(texto)
        var mejor: Version? = null
        for (i in 0 until lista.length()) {
            val r = lista.getJSONObject(i)
            val tag = r.optString("tag_name")
            if (r.optBoolean("draft") || !tag.startsWith(PREFIJO)) continue
            val nombre = tag.removePrefix(PREFIJO)
            if (compara(nombre, versionActual) <= 0) continue
            if (mejor != null && compara(nombre, mejor.nombre) <= 0) continue
            val assets = r.optJSONArray("assets") ?: continue
            for (j in 0 until assets.length()) {
                val a = assets.getJSONObject(j)
                if (!a.optString("name").endsWith(".apk")) continue
                mejor = Version(
                    nombre = nombre,
                    notas = r.optString("body").trim(),
                    url = a.optString("browser_download_url"),
                    bytes = a.optLong("size"),
                    sha256 = a.optString("digest").takeIf { it.startsWith("sha256:") }?.removePrefix("sha256:"),
                    pagina = r.optString("html_url"),
                )
                break
            }
        }
        return mejor
    }

    fun descargar(v: Version) {
        if (tarea?.isActive == true) return
        _pospuesta.value = null
        _estado.value = Act.Descargando(v, 0f)
        tarea = alcance.launch {
            _estado.value = try {
                Act.Lista(v, bajar(v))
            } catch (e: Exception) {
                Log.w(TAG, "descargar actualización", e)
                Act.Error(e.message ?: "La descarga falló", v)
            }
        }
    }

    private fun bajar(v: Version): File {
        if (!v.url.startsWith("https://github.com/")) throw SecurityException("Enlace de descarga inesperado")
        val dir = File(app.cacheDir, "actualizacion").apply { deleteRecursively(); mkdirs() }
        val destino = File(dir, "nullcell-${v.nombre}.apk")
        val sha = MessageDigest.getInstance("SHA-256")
        val c = (URL(v.url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000; readTimeout = 30_000
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "nullcell-android")
        }
        try {
            if (c.responseCode != 200) throw IllegalStateException("GitHub respondió ${c.responseCode}")
            val total = c.contentLengthLong.takeIf { it > 0 } ?: v.bytes
            var leidos = 0L
            var ultimo = 0f
            c.inputStream.use { ent ->
                destino.outputStream().use { sal ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = ent.read(buf); if (n < 0) break
                        sal.write(buf, 0, n); sha.update(buf, 0, n); leidos += n
                        val p = if (total > 0) leidos.toFloat() / total else 0f
                        if (p - ultimo >= .01f) { ultimo = p; _estado.value = Act.Descargando(v, p) }
                    }
                }
            }
        } finally { c.disconnect() }

        val hex = sha.digest().joinToString("") { "%02x".format(it) }
        if (v.sha256 != null && !hex.equals(v.sha256, ignoreCase = true)) {
            destino.delete(); throw SecurityException("El archivo descargado no coincide con el publicado (SHA-256). Descartado.")
        }
        // Android rechazaría igualmente una firma distinta, pero así se dice por qué.
        val nuevo = infoArchivo(destino) ?: run { destino.delete(); throw SecurityException("El archivo descargado no es un APK válido") }
        if (nuevo.packageName != app.packageName) { destino.delete(); throw SecurityException("El APK es de otra app (${nuevo.packageName})") }
        if (firmas(nuevo) != firmas(info(app.packageName))) {
            destino.delete()
            throw SecurityException("El APK nuevo está firmado con otra clave que la app instalada. No se instala.")
        }
        return destino
    }

    /** Abre el instalador de Android. Si falta el permiso, lleva a activarlo. */
    fun instalar(ctx: Context, apk: File) {
        if (Build.VERSION.SDK_INT >= 26 && !ctx.packageManager.canRequestPackageInstalls()) {
            ctx.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            return
        }
        val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.archivos", apk)
        ctx.startActivity(
            Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    fun puedeInstalar(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 26 || ctx.packageManager.canRequestPackageInstalls()

    // ---------------------------------------------------------------- ayudas

    /** Compara "0.1.10" con "0.1.9" número a número. */
    fun compara(a: String, b: String): Int {
        val x = a.split('.', '-').map { it.toIntOrNull() ?: 0 }
        val y = b.split('.', '-').map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) {
            val d = (x.getOrElse(i) { 0 }).compareTo(y.getOrElse(i) { 0 })
            if (d != 0) return d
        }
        return 0
    }

    @Suppress("DEPRECATION")
    private val banderas: Int
        get() = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES

    private fun info(paquete: String): PackageInfo? = try {
        app.packageManager.getPackageInfo(paquete, banderas)
    } catch (_: Exception) { null }

    private fun infoArchivo(f: File): PackageInfo? =
        app.packageManager.getPackageArchiveInfo(f.absolutePath, banderas)

    @Suppress("DEPRECATION")
    private fun firmas(p: PackageInfo?): Set<String> {
        if (p == null) return emptySet()
        val sigs = if (Build.VERSION.SDK_INT >= 28) p.signingInfo?.apkContentsSigners else p.signatures
        val md = MessageDigest.getInstance("SHA-256")
        return sigs.orEmpty().map { s -> md.digest(s.toByteArray()).joinToString("") { "%02x".format(it) } }.toSet()
    }
}
