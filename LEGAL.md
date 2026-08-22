# Legal — Onionmind (EU law framing)

*This page documents the legal posture of Onionmind. It is not legal advice;
consult a lawyer for your specific situation.*

## Software license

Onionmind's own code is released under the **MIT License** ([LICENSE](LICENSE)).
You may use, modify and redistribute it, including commercially, with attribution.

## Model weights: Apache-2.0, redistribution permitted

Every model this project ships is licensed **Apache-2.0** (verified against the
Hugging Face repository metadata, August 2026):

| Use | Repository | License |
|:--|:--|:--|
| spark (4B) | [`mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF`](https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF) | Apache-2.0 |
| torch (9B) | [`mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF`](https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF) | Apache-2.0 |
| inferno (27B, 12GB build) | [`soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf`](https://huggingface.co/soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf) | Apache-2.0 |
| inferno (27B, other builds) | [`hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF`](https://huggingface.co/hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF) | Apache-2.0 |
| inferno-vision (mmproj) | [`JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`](https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF) | Apache-2.0 |

Apache-2.0 permits redistribution of the weights, including inside the
Matchstick images and the pre-built stick release, provided the license text
and attribution travel along (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
The base models descend from Alibaba's Qwen family under Apache-2.0; the
"abliteration" (refusal-behaviour removal) was performed by the upstream
repositories listed above, not by this project.

## EU AI Act (Regulation (EU) 2024/1689)

- Onionmind is a **local, personal tool**: it is not "placed on the market" or
  "put into service" as a service by this project, which is the trigger for
  most system-level obligations.
- The models are distributed under a **royalty-free open-source license**,
  which engages the AI Act's open-source carve-outs (Art. 2(12); Recital 107):
  most GPAI-provider obligations do not apply.
- The **systemic-risk regime** (Art. 3(63)) attaches to models *trained* with
  more than 10²⁵ FLOPs. Nothing here was trained by this project; the models
  are far below that scale.
- **Transparency (Art. 50(1))**: the interface and documentation state plainly
  that you are interacting with an AI system.
- This project **processes no personal data** (GDPR, Reg. (EU) 2016/679):
  everything runs locally; only search queries transit the Tor network.

## Anonymity tooling is lawful

Using, running and distributing Tor and related privacy tools is legal across
the EU and EEA, including in Norway. MAC randomization of one's own devices,
RAM-only ("amnesic") operation, and per-request circuit isolation are lawful
privacy measures.

## Web search

The search agent queries DuckDuckGo's public HTML endpoints on an occasional,
per-question basis for personal use. Under the Database Directive (96/9/EC),
extraction of insubstantial parts for private purposes is the tolerated end of
the spectrum; this project does not systematically scrape, republish, or
commercialize results.

## User responsibility

An uncensored model removes *refusals*, not *laws*. Whatever is unlawful to
produce, possess or distribute remains unlawful when produced with this tool —
under EU law and its national implementations (e.g. Directive 2011/93/EU on
child sexual abuse, Regulation (EU) 2021/784 on terrorist content) and, in
Norway, the general criminal code (straffeloven). Responsibility for generated
content lies with the user.

## Norway (EEA)

Norway is bound by the AI Act only once it is incorporated into the EEA
Agreement; as of August 2026 this is still pending, with application expected
around August 2027. Until then, Norwegian general law applies (copyright under
åndsverksloven, including its database provisions; the penal code; the
Personal Data Act implementing the GDPR, which this project's local-only
architecture keeps it outside of).

## No warranty

Provided "as is", without warranty of any kind (MIT License, §4–§6). Nothing
here is a solicitation or offer to provide legal services.
