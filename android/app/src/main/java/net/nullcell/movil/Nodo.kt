package net.nullcell.movil

import android.content.Context
import android.util.Log
import com.chaquo.python.Python
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Lo que la interfaz necesita saber del nodo, en un solo sitio. */
data class Estado(
    val fase: Fase = Fase.ARRANCANDO,
    val error: String? = null,
    val nid: String? = null,
    val celdas: Int = 0,
    val relevos: Int = 0,
    val operadores: Int = 0,
    val necesarios: Int = 3,
    val saltos: Int = 0,
    val anonimo: Boolean = false,
    val rutasListas: Boolean = false,
    val porQueNo: String? = null,
    val listoEn: Double? = null,
    val circuitos: Int = 0,
    val registros: Int = 0,
    val invitacion: String? = null,
    val eventos: List<Evento> = emptyList(),
)

enum class Fase { ARRANCANDO, CONECTANDO, EN_RED, ERROR }

data class Evento(val tipo: String, val texto: String, val ts: Double)

data class Semilla(
    val linea: String, val nid: String, val endpoint: String, val origen: String,
    val nota: String, val verificada: Boolean, val publica: Boolean,
    val ultimoOk: Double?, val fallos: Int,
)

data class Semillas(
    val lista: List<Semilla> = emptyList(), val total: Int = 0, val verificadas: Int = 0,
    val recibidas: Int = 0, val nuevas: Int = 0, val compartidas: Int = 0,
    val propia: String? = null, val invitacion: String? = null,
)

/** El chat: canal actual, quién está y lo que se ha dicho mientras estás. */
data class Chat(
    val canal: String? = null,
    val apodo: String? = null,
    val pedido: String? = null,
    val listo: Boolean = false,
    val porQueNo: String? = null,
    val conectados: Int = 0,
    val miembros: List<Miembro> = emptyList(),
    val mensajes: List<MensajeChat> = emptyList(),
)

data class Miembro(val apodo: String, val direccion: String, val conectado: Boolean)
data class MensajeChat(val id: String, val apodo: String, val texto: String, val ts: Double, val mio: Boolean, val autor: String)
data class Canal(val nombre: String, val personas: Int, val dentro: Boolean)

data class Resultado(
    val nid: String, val titulo: String, val desc: String, val tags: List<String>,
    val porque: String, val tipo: String,
)

/**
 * El nodo Nullcell del teléfono. Python lo ejecuta en su propio hilo
 * (`nullcell_movil.arrancar`); aquí sólo se habla con su API local —la misma
 * que el panel de escritorio, en 127.0.0.1 y con token— y se piden páginas.
 */
object Nodo {
    private const val TAG = "Nullcell"
    private lateinit var home: File
    private val alcance = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var hud: String? = null
    private var token: String? = null
    private var sondeo: Job? = null

    private val _estado = MutableStateFlow(Estado())
    val estado: StateFlow<Estado> = _estado.asStateFlow()
    private val _semillas = MutableStateFlow(Semillas())
    val semillas: StateFlow<Semillas> = _semillas.asStateFlow()
    private val _chat = MutableStateFlow(Chat())
    val chat: StateFlow<Chat> = _chat.asStateFlow()
    /** Mensajes de otros que llegaron mientras no mirabas el chat. */
    private val _sinLeer = MutableStateFlow(0)
    val sinLeer: StateFlow<Int> = _sinLeer.asStateFlow()
    @Volatile var mirandoChat = false
    private var ultimoLeido = 0.0
    private var reentrado = false
    private lateinit var prefs: android.content.SharedPreferences

    fun init(ctx: Context) {
        home = File(ctx.filesDir, "nullcell")
        prefs = ctx.getSharedPreferences("chat", Context.MODE_PRIVATE)
        ultimoLeido = System.currentTimeMillis() / 1000.0
    }

    /** El apodo que se recuerda entre sesiones (el que pediste, sin número). */
    var apodoGuardado: String?
        get() = prefs.getString("apodo", null)
        set(v) { prefs.edit().putString("apodo", v).apply() }
    /** El último canal: al volver a abrir la app se vuelve a entrar solo. */
    var canalGuardado: String?
        get() = prefs.getString("canal", null)
        set(v) { prefs.edit().putString("canal", v).apply() }

    /** Arranca (una vez) y empieza a mirar el estado cada pocos segundos. */
    fun arrancar() {
        if (sondeo?.isActive == true) return
        sondeo = alcance.launch {
            if (hud == null) {
                _estado.value = _estado.value.copy(fase = Fase.ARRANCANDO, error = null)
                try {
                    val info = JSONObject(
                        Python.getInstance().getModule("nullcell_movil")
                            .callAttr("arrancar", home.absolutePath).toString()
                    )
                    hud = info.getString("hud"); token = info.getString("token")
                    _estado.value = _estado.value.copy(fase = Fase.CONECTANDO, nid = info.optString("nid"))
                } catch (e: Throwable) {
                    Log.e(TAG, "el nodo no arrancó", e)
                    _estado.value = _estado.value.copy(fase = Fase.ERROR, error = e.message ?: e.toString())
                    return@launch
                }
            }
            while (isActive) {
                runCatching { refrescar() }.onFailure { Log.w(TAG, "sondeo: $it") }
                delay(3000)
            }
        }
    }

    suspend fun refrescar() = withContext(Dispatchers.IO) {
        val st = api("GET", "/api/state") ?: return@withContext
        val ct = api("GET", "/api/control") ?: JSONObject()
        val s = st.optJSONObject("state") ?: JSONObject()
        val red = ct.optJSONObject("network") ?: JSONObject()
        val eventos = st.optJSONArray("events") ?: JSONArray()
        val listos = ct.optBoolean("paths_ready")
        _estado.value = Estado(
            fase = if (listos || s.optInt("peers") > 0) Fase.EN_RED else Fase.CONECTANDO,
            nid = s.optString("nid", _estado.value.nid ?: ""),
            celdas = s.optInt("peers"),
            relevos = red.optInt("usable", s.optInt("relays")),
            operadores = red.optInt("operators"),
            necesarios = red.optInt("needed", 3),
            saltos = ct.optInt("hops", 0),
            anonimo = ct.optBoolean("anonymous"),
            rutasListas = listos,
            porQueNo = ct.optString("why_not_ready").ifBlank { null },
            listoEn = if (red.isNull("ready_in")) null else red.optDouble("ready_in"),
            circuitos = s.optJSONArray("circuits")?.length() ?: 0,
            registros = s.optInt("records"),
            invitacion = ct.optString("invitation").ifBlank { null },
            eventos = (0 until eventos.length()).mapNotNull { i ->
                eventos.optJSONObject(i)?.let {
                    Evento(it.optString("kind"), it.optString("text"), it.optDouble("ts"))
                }
            }.reversed().take(30),
        )
        api("GET", "/api/seeds")?.let { _semillas.value = parseSemillas(it) }
        refrescarChat()
        // Volver a entrar solo al último canal, una vez por sesión del nodo.
        if (!reentrado && listos && _chat.value.canal == null) {
            val apodo = apodoGuardado; val canal = canalGuardado
            if (apodo != null && canal != null) { reentrado = true; chatEntrar(canal, apodo) }
        }
    }

    // ---------------------------------------------------------------- chat
    suspend fun refrescarChat() = withContext(Dispatchers.IO) {
        val j = api("GET", "/api/chat") ?: return@withContext
        val ms = j.optJSONArray("messages") ?: JSONArray()
        val mb = j.optJSONArray("members") ?: JSONArray()
        val c = Chat(
            canal = j.optString("channel").takeIf { !j.isNull("channel") && it.isNotBlank() },
            apodo = j.optString("nick").ifBlank { null },
            pedido = j.optString("wanted").ifBlank { null },
            listo = j.optBoolean("ready"),
            porQueNo = j.optString("why_not_ready").ifBlank { null },
            conectados = j.optInt("connected"),
            miembros = (0 until mb.length()).map { i ->
                val o = mb.getJSONObject(i)
                Miembro(o.optString("nick"), o.optString("address"), o.optBoolean("connected"))
            },
            mensajes = (0 until ms.length()).map { i ->
                val o = ms.getJSONObject(i)
                MensajeChat(o.optString("mid"), o.optString("nick"), o.optString("text"),
                    o.optDouble("ts"), o.optBoolean("mine"), o.optString("author"))
            },
        )
        _chat.value = c
        if (mirandoChat) marcarLeido()
        else _sinLeer.value = c.mensajes.count { !it.mio && it.ts > ultimoLeido }
    }

    fun marcarLeido() {
        ultimoLeido = maxOf(ultimoLeido, _chat.value.mensajes.maxOfOrNull { it.ts } ?: 0.0)
        _sinLeer.value = 0
    }

    suspend fun chatEntrar(canal: String, apodo: String): Result<String> = withContext(Dispatchers.IO) {
        val r = api("POST", "/api/chat/join", JSONObject().put("channel", canal).put("nick", apodo), timeoutMs = 60_000)
            ?: return@withContext Result.failure(Exception("El nodo no responde"))
        r.optString("error").ifBlank { null }?.let { return@withContext Result.failure(Exception(it)) }
        apodoGuardado = apodo.trim()
        canalGuardado = r.optString("channel").ifBlank { canal }
        refrescarChat()
        val c = _chat.value
        Result.success(
            if (c.apodo != null && c.pedido != null && !c.apodo.equals(c.pedido, ignoreCase = false))
                "«${c.pedido}» ya estaba en uso: entras como ${c.apodo}"
            else "Entraste en #${c.canal}"
        )
    }

    suspend fun chatDecir(texto: String): Result<Unit> = withContext(Dispatchers.IO) {
        val r = api("POST", "/api/chat/say", JSONObject().put("text", texto), timeoutMs = 30_000)
            ?: return@withContext Result.failure(Exception("El nodo no responde"))
        r.optString("error").ifBlank { null }?.let { return@withContext Result.failure(Exception(it)) }
        refrescarChat()
        Result.success(Unit)
    }

    suspend fun chatSalir() = withContext(Dispatchers.IO) {
        api("POST", "/api/chat/leave", JSONObject())
        canalGuardado = null
        refrescarChat()
    }

    suspend fun chatCanales(): List<Canal> = withContext(Dispatchers.IO) {
        val r = api("GET", "/api/chat/channels", timeoutMs = 30_000) ?: return@withContext emptyList()
        val a = r.optJSONArray("channels") ?: JSONArray()
        (0 until a.length()).map { i ->
            val o = a.getJSONObject(i)
            Canal(o.optString("channel"), o.optInt("members"), o.optBoolean("joined"))
        }
    }

    private fun parseSemillas(j: JSONObject): Semillas {
        val arr = j.optJSONArray("seeds") ?: JSONArray()
        val stats = j.optJSONObject("stats") ?: JSONObject()
        return Semillas(
            lista = (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Semilla(
                    linea = o.optString("line"), nid = o.optString("nid"),
                    endpoint = o.optString("endpoint"), origen = o.optString("source"),
                    nota = o.optString("note"), verificada = o.optBoolean("verified"),
                    publica = o.optBoolean("public"),
                    ultimoOk = if (o.isNull("last_ok")) null else o.optDouble("last_ok"),
                    fallos = o.optInt("fails"),
                )
            },
            total = j.optInt("total"), verificadas = j.optInt("verified"),
            recibidas = stats.optInt("recibidas"), nuevas = stats.optInt("nuevas"),
            compartidas = stats.optInt("compartidas"),
            propia = j.optString("own").ifBlank { null },
            invitacion = j.optString("invitation").ifBlank { null },
        )
    }

    // ------------------------------------------------------------ acciones
    /** Devuelve un mensaje para enseñar, bueno o malo. */
    suspend fun agregarSemilla(linea: String, nota: String): Result<String> = withContext(Dispatchers.IO) {
        val r = api("POST", "/api/seeds", JSONObject().put("seed", linea.trim()).put("note", nota.trim()))
            ?: return@withContext Result.failure(Exception("El nodo no responde"))
        refrescar()
        r.optString("error").ifBlank { null }?.let { Result.failure(Exception(it)) }
            ?: Result.success(r.optString("detail", "Semilla añadida"))
    }

    suspend fun quitarSemilla(linea: String): Result<String> = withContext(Dispatchers.IO) {
        val r = api("DELETE", "/api/seeds", JSONObject().put("seed", linea))
            ?: return@withContext Result.failure(Exception("El nodo no responde"))
        refrescar()
        r.optString("error").ifBlank { null }?.let { Result.failure(Exception(it)) }
            ?: Result.success("Semilla quitada")
    }

    suspend fun intercambiar(): Result<String> = withContext(Dispatchers.IO) {
        val r = api("POST", "/api/seeds/exchange", JSONObject(), timeoutMs = 90_000)
            ?: return@withContext Result.failure(Exception("El nodo no responde"))
        refrescar()
        r.optString("error").ifBlank { null }?.let { Result.failure(Exception(it)) }
            ?: Result.success(r.optString("detail", "Hecho"))
    }

    suspend fun buscar(q: String): Result<List<Resultado>> = withContext(Dispatchers.IO) {
        val r = api("GET", "/api/search?q=" + URLEncoder.encode(q, "UTF-8"), timeoutMs = 30_000)
            ?: return@withContext Result.failure(Exception("El nodo no responde"))
        val arr = r.optJSONArray("results") ?: JSONArray()
        Result.success((0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            val tags = o.optJSONArray("tags") ?: JSONArray()
            Resultado(
                nid = o.optString("nid"), titulo = o.optString("title"),
                desc = o.optString("desc"),
                tags = (0 until tags.length()).map { tags.getString(it) },
                porque = o.optString("why"), tipo = o.optString("kind"),
            )
        })
    }

    /** Una página de un portal, para el WebView. Bloquea: llamar fuera del hilo principal. */
    fun cargar(url: String): Pagina {
        val r = Python.getInstance().getModule("nullcell_movil").callAttr("cargar", url)
        fun s(k: String) = r.callAttr("get", k)?.toString() ?: ""
        val cuerpo = r.callAttr("get", "body")?.toJava(ByteArray::class.java) ?: ByteArray(0)
        val cab = runCatching { JSONObject(s("headers")) }.getOrDefault(JSONObject())
        return Pagina(s("status").toIntOrNull() ?: 500, s("type"), s("charset"), cab, cuerpo)
    }

    // --------------------------------------------------------------- HTTP
    private fun api(metodo: String, ruta: String, cuerpo: JSONObject? = null, timeoutMs: Int = 15_000): JSONObject? {
        val base = hud ?: return null
        val c = URL(base + ruta).openConnection() as HttpURLConnection
        return try {
            c.requestMethod = metodo
            c.connectTimeout = 5000; c.readTimeout = timeoutMs
            c.setRequestProperty("X-Nullcell-Token", token)
            c.setRequestProperty("Accept", "application/json")
            if (cuerpo != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(cuerpo.toString().toByteArray()) }
            }
            val flujo = if (c.responseCode < 400) c.inputStream else c.errorStream
            val texto = flujo?.bufferedReader()?.readText().orEmpty()
            if (texto.isBlank()) JSONObject() else JSONObject(texto)
        } catch (e: Exception) {
            Log.w(TAG, "$metodo $ruta: $e"); null
        } finally { c.disconnect() }
    }
}

class Pagina(val status: Int, val tipo: String, val charset: String, val cabeceras: JSONObject, val cuerpo: ByteArray)
