pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
        // AndroidLibXrayLite — то же ядро Xray, что использует v2rayNG.
        maven { url = uri("https://jitpack.io") }
    }
}

rootProject.name = "TargetVPN"
include(":app")
