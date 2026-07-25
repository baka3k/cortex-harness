plugins {
    id("com.android.application")
    kotlin("android")
}

android {
    namespace = "example.app"
    defaultConfig { applicationId = "example.app" }
    dynamicFeatures += setOf(":feature")
}

dependencies {
    implementation(project(":library"))
    implementation("org.springframework:spring-core:6.2.0")
}
