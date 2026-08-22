// The pure-logic core: SOCKS5-with-auth, the Tor search agent ported from
// onionmind.py, and the llama-server chat client. NO android imports - this
// module runs on a desktop JVM too, which is how the logic gets tested
// against a real tor daemon without a phone.
plugins {
    id("org.jetbrains.kotlin.jvm")
    id("org.jetbrains.kotlin.plugin.serialization")
}
kotlin { jvmToolchain(17) }
dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")
    testImplementation(kotlin("test"))
}
tasks.test {
    // Network tests are opt-in: -Ponionmind.net.tests=true (see android/itest.sh)
    if (!project.hasProperty("onionmind.net.tests")) {
        exclude("**/*NetTest*")
    }
}
