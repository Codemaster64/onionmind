import java.net.URI

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
}
android {
    namespace = "org.onionmind.app"
    compileSdk = 34
    defaultConfig {
        applicationId = "org.onionmind.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 5
        versionName = "1.4"
        ndk { abiFilters += listOf("arm64-v8a") }   // phones this app is for are all arm64
        val modelMirrorBase = providers.gradleProperty("modelMirrorBase").orNull?.trimEnd('/') ?: ""
        require(modelMirrorBase.isEmpty() || "https".equals(URI(modelMirrorBase).scheme, ignoreCase = true)) {
            "modelMirrorBase must use HTTPS"
        }
        val escapedModelMirrorBase = modelMirrorBase
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
        buildConfigField("String", "MODEL_MIRROR_BASE", "\"$escapedModelMirrorBase\"")
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }
    buildFeatures { buildConfig = true }
    // extractNativeLibs: llama-server and tor must be real files on disk to exec()
    packagingOptions { jniLibs { useLegacyPackaging = true } }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}
dependencies {
    implementation(project(":core"))
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")
    implementation("org.nanohttpd:nanohttpd:2.3.1")
    // Prebuilt tor for Android - the same binary Orbot runs, straight from Maven.
    implementation("info.guardianproject:tor-android:0.4.7.14")
}
