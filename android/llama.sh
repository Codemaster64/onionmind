#!/usr/bin/env bash
# Cross-compiles llama.cpp's llama-server for Android arm64 with the NDK, and
# drops it in jniLibs under a lib*.so name - the only name Android both
# packages and keeps as a real, executable file on disk.
set -e
cd "$(dirname "$0")"
OUT="app/src/main/jniLibs/arm64-v8a/libllamaserver.so"
[ -f "$OUT" ] && { echo "llama-server: cached"; exit 0; }

NDK="${ANDROID_NDK_HOME:-$ANDROID_HOME/ndk/26.3.11579264}"
rm -rf /tmp/llama
git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama
cmake -S /tmp/llama -B /tmp/llama/build \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 \
  -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_OPENMP=OFF \
  -DLLAMA_BUILD_UI=OFF >/dev/null
# ^ the server's embedded web UI builds a HOST tool, which does not survive
#   cross-compilation; the app ships its own chat UI anyway.
cmake --build /tmp/llama/build --target llama-server -j"$(nproc)"
mkdir -p "$(dirname "$OUT")"
cp /tmp/llama/build/bin/llama-server "$OUT"
# llama-server is a thin stub; the engine ships as shared libs alongside it.
# They are already lib*-named, so they package as jniLibs and get extracted
# next to the stub - LD_LIBRARY_PATH points the linker there at runtime.
cp /tmp/llama/build/bin/*.so "$(dirname "$OUT")/"
# Release builds still carry full debug info - 200MB+ of symbols. Strip them.
STRIP="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip"
"$STRIP" "$OUT" "$(dirname "$OUT")"/*.so
echo "llama-server: built $(du -h "$OUT" | cut -f1) + $(ls "$(dirname "$OUT")"/*.so | wc -l) libs, $(du -sh "$(dirname "$OUT")" | cut -f1) total"
