plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

android {
    namespace = "net.nullcell.movil"
    compileSdk = 35

    defaultConfig {
        applicationId = "net.nullcell.movil"
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "0.1.2"
        // Teléfonos de los últimos ~8 años. Cada ABI añade ~25 MB de Python.
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    // Clave de publicación: SIEMPRE la misma, o Android rechaza las actualizaciones.
    // Vive fuera del repo; construir.sh la monta y pasa su ruta y contraseña.
    val almacen = System.getenv("NULLCELL_FIRMA")?.let { file(it) }?.takeIf { it.exists() }
    signingConfigs {
        if (almacen != null) create("publicacion") {
            storeFile = almacen
            storePassword = System.getenv("NULLCELL_FIRMA_CLAVE")
            keyAlias = "nullcell"
            keyPassword = System.getenv("NULLCELL_FIRMA_CLAVE")
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("publicacion")
                ?: error("Falta la clave de firma (NULLCELL_FIRMA): sin ella las actualizaciones no instalarían")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        buildPython(System.getenv("NULLCELL_BUILD_PYTHON") ?: "python3")
        pip {
            // Ruedas puras preparadas por ../preparar-ruedas.sh (incluye nullcell
            // y aioquic sin su pieza nativa). Lo criptográfico lo pone Chaquopy.
            options("--find-links", "${rootDir}/ruedas")
            // Lo nativo, en las versiones que Chaquopy compila para Android.
            // La suite de Nullcell pasa entera con estas (ver LEEME.md).
            install("cryptography==42.0.8")
            install("PyNaCl==1.5.0")
            install("pyOpenSSL<25")
            // aioquic pide service-identity sin tope, y la 26 exige cryptography>=47
            // (importa cryptography.hazmat.asn1): con la 42.0.8 el nodo no arrancaba.
            // Chaquopy instala lo nativo aparte y no ve el choque; verificar-apk.sh sí.
            install("service-identity==24.2.0")
            // Versión exacta de cada rueda preparada: si no, pip prefiere una
            // más nueva de PyPI que no tiene rueda para Android (cbor2 6 pide
            // Rust, aiohttp nativo…) e intenta compilarla.
            rootDir.resolve("ruedas").listFiles { f -> f.name.endsWith("-py3-none-any.whl") }
                ?.forEach { f ->
                    val (nombre, version) = f.name.split("-").let { it[0] to it[1] }
                    install("$nombre==$version")
                }
        }
    }
}

dependencies {
    val bom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(bom)
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.animation:animation")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
