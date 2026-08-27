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
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
tasks.test {
    // Network tests are opt-in: -Ponionmind.net.tests=true (see android/itest.sh)
    if (!project.hasProperty("onionmind.net.tests")) {
        exclude("**/*NetTest*")
    }
    // The Kotlin parser is a port of onionmind.py's; point it at the SAME
    // fixture the python suite uses so the two cannot drift apart unnoticed.
    systemProperty("onionmind.fixtures", rootProject.projectDir.parentFile.resolve("tests").absolutePath)
    testLogging { showStandardStreams = true }
}
