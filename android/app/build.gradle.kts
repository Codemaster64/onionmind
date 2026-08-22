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
        versionCode = 2
        versionName = "1.1"
        ndk { abiFilters += listOf("arm64-v8a") }   // phones this app is for are all arm64
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
        }
    }
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
