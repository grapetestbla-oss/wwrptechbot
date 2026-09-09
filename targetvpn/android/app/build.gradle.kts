plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "us.targetvpn.client"
    compileSdk = 34

    defaultConfig {
        applicationId = "us.targetvpn.client"
        minSdk = 24
        targetSdk = 34
        versionCode = 8
        versionName = "2.3.0"

        // Адрес бэкенда подставляется при сборке: -PapiBase=https://ваш-домен
        buildConfigField("String", "API_BASE",
            "\"${project.findProperty("apiBase") ?: "https://example.com"}\"")
    }

    // Ядро занимает по 34 МБ на архитектуру. Отдельные APK под каждую
    // избавляют пользователя от лишней половины.
    splits {
        abi {
            isEnable = true
            reset()
            include("arm64-v8a", "armeabi-v7a")
            isUniversalApk = false
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    // Ядро туннеля: Xray + tun2socks, собранные gomobile в targetcore.aar.
    implementation(files("libs/targetcore.aar"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.json:json:20240303")
}
