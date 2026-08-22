# Third-party notices

Onionmind bundles, downloads or builds upon the following. Each license
permits redistribution; attribution and license texts travel with this file.

## Model weights (Apache-2.0)

Distributed under the Apache License, Version 2.0 — full text:
https://www.apache.org/licenses/LICENSE-2.0

- `mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF` (spark) — quantization of
  `huihui-ai/Huihui-Qwen3.5-4B-abliterated`, itself an abliteration of the
  Qwen3.5-4B base model (Alibaba, Apache-2.0).
- `mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF` (torch) — as above, 9B.
- `soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf` (inferno, 12GB
  build) — 3.69bpw mixed-precision build of the Qwen3.8-27B abliteration.
- `hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF` (inferno, Q4_K_M / IQ2_M builds).
- `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF` (inferno-vision mmproj).

All weights are re-distributed unmodified from the repositories above; the
abliteration modifications were made upstream, not by this project.

## llama.cpp (MIT)

https://github.com/ggml-org/llama.cpp — the inference engine. Built from
source for each target (Android arm64 via NDK; the Linux live image via
live-build). Permission is hereby granted free of charge to copy, modify,
merge, publish, distribute and/or sell copies subject to including the above
copyright notice and permission notice: Copyright (c) 2023-2026 The ggml
authors. Full text: https://github.com/ggml-org/llama.cpp/blob/master/LICENSE

## tor (BSD-3-Clause)

https://gitlab.torproject.org/tpo/core/tor — Copyright (c) The Tor Project,
Inc. Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the BSD-3-Clause conditions are met
(retain the copyright notice, the disclaimer, and don't use the names to
endorse derived products): https://gitlab.torproject.org/tpo/core/tor/-/raw/main/LICENSE
The Android APK embeds the prebuilt binary from
`info.guardianproject:tor-android` (Guardian Project).

## Ollama (MIT)

https://github.com/ollama/ollama — downloaded (not bundled) by the desktop
installers. Copyright (c) Ollama contributors.

## Android APK dependencies

- OkHttp (`com.squareup.okhttp3`) — Apache-2.0, Square Inc.
- NanoHTTPD (`org.nanohttpd`) — BSD-3-Clause, Ricardo Marx & contributors
- kotlinx.serialization — Apache-2.0, JetBrains s.r.o.
- Android SDK components — used under the Android Software Development Kit
  License Agreement.

## Matchstick live image (Debian)

The live image contains unmodified binary packages from Debian GNU/Linux
(`main`, `contrib`, `non-free-firmware`), redistributed under each package's
own license per Debian's policy, including the NVIDIA proprietary driver
packaging from `non-free` (NVIDIA permits redistribution of unmodified
driver packages; see Debian's non-free legal position) and CPU/Wi-Fi firmware
blobs from `non-free-firmware`. Sources for all Debian packages are available
at http://deb.debian.org/debian/.
