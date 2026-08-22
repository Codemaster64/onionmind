# Build image for the Onionmind Android APK. Two stages so the 4GB of SDK/NDK
# setup is cached: change app sources and rebuilds skip straight to gradle.
#
#   docker build -f android/Dockerfile -t onionmind-android .
#   docker run --rm -v "$(pwd):/repo" -v "$(pwd)/../onionmind-out:/out" \
#     onionmind-android            # writes /out/Onionmind-1.2.apk
#
FROM gradle:8-jdk17 AS sdk
ENV ANDROID_HOME=/opt/android-sdk
RUN apt-get update && apt-get install -y --no-install-recommends \
      wget unzip git imagemagick tor ca-certificates >/dev/null && rm -rf /var/lib/apt/lists/*
RUN mkdir -p $ANDROID_HOME/cmdline-tools && \
    wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -O /tmp/clt.zip && \
    unzip -q /tmp/clt.zip -d $ANDROID_HOME/cmdline-tools && \
    mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest
ENV PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$PATH
RUN yes | sdkmanager --licenses >/dev/null && \
    sdkmanager "platforms;android-34" "build-tools;34.0.0" \
               "ndk;26.3.11579264" "platform-tools" >/dev/null

FROM sdk AS build
# g++ matters: llama.cpp's server embeds its web UI via a HOST-compiled tool;
# upstream cross-builds it by finding a host g++ when CMAKE_CROSSCOMPILING.
RUN apt-get update && apt-get install -y --no-install-recommends cmake ninja-build g++ >/dev/null && rm -rf /var/lib/apt/lists/*
COPY . /repo
WORKDIR /repo
RUN bash android/icons.sh && bash android/llama.sh
WORKDIR /repo/android
RUN bash itest.sh && gradle -q assembleDebug
RUN mkdir -p /out-staging && cp app/build/outputs/apk/debug/app-debug.apk /out-staging/Onionmind-1.2.apk
CMD ["bash", "-c", "mkdir -p /out && cp /out-staging/Onionmind-1.2.apk /out/ && echo 'APK -> /out/Onionmind-1.2.apk'"]
