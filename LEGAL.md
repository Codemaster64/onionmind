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
| EMBER (4B) | [`mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF`](https://huggingface.co/mradermacher/Huihui-Qwen3.5-4B-abliterated-GGUF) | Apache-2.0 |
| BLAZE (9B) | [`mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF`](https://huggingface.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF) | Apache-2.0 |
| INFERNO (27B, 12GB build) | [`soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf`](https://huggingface.co/soyaakinohara/qwen3.8-27b-abliterated-3.69bpw-12GB-MTP.gguf) | Apache-2.0 |
| INFERNO (27B, other builds) | [`hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF`](https://huggingface.co/hotdogs/Qwen3.8-27B-abliterated-MTP-GGUF) | Apache-2.0 |
| INFERNO-VISION (mmproj) | [`JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`](https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF) | Apache-2.0 |

Apache-2.0 permits redistribution of the weights, including inside the
Matchstick images and the pre-built stick release, provided the license text
and attribution travel along (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
The base models descend from Alibaba's Qwen family under Apache-2.0; the
"abliteration" (refusal-behaviour removal) was performed by the upstream
repositories listed above, not by this project.

## EU AI Act (Regulation (EU) 2024/1689)

**Role in the supply chain.** This project did not train, fine-tune, quantise or
"abliterate" any model. The weights are taken unmodified from the third-party
repositories listed above, under Apache-2.0. Onionmind is a launcher and a search
integration around a third-party runtime (Ollama / llama.cpp) and third-party
weights. It is not the *provider* of a general-purpose AI model and does not
place a model on the market; it distributes an integration under a free and
open-source licence, with no monetisation.

- This is a local, personal tool, but that description is not by itself an
  exemption from the AI Act or a conclusion about whether an obligation applies.
- To the extent the AI Act applies, this project relies on the open-source
  exception where applicable. The exception has conditions and is not automatic;
  this is not a guarantee or a definitive legal conclusion.
- The **systemic-risk regime** (Art. 3(63)) attaches to models *trained* with
  more than 10²⁵ FLOPs. Nothing here was trained by this project; the models
  are far below that scale.
- Onionmind performs none of the practices prohibited by Art. 5 and falls under
  no Annex III high-risk use. If the AI Act applies, the duties that remain are
  the Art. 50 transparency duties.
- **Transparency (Art. 50(1))**: the interface and the documentation state
  plainly that you are interacting with an AI system.
- The maintainers do not collect, store, or process user prompts or outputs.
  Prompts may contain personal data and are processed locally on the user's
  device. When web search is used, the query is sent to DuckDuckGo over Tor and
  is visible to DuckDuckGo.

## Product liability (Directive (EU) 2024/2853)

The revised Product Liability Directive treats software — including AI systems —
as a *product*, and applies to products placed on the market from 9 December 2026.
It also brings evidence-disclosure duties and presumptions of defectiveness that
make a defence on the merits expensive even when it wins. What matters for this
project is therefore scope, not merits.

**The Directive excludes free and open-source software developed or supplied
outside the course of a commercial activity** (Art. 2(2), Recital 14). Onionmind
is supplied under the MIT License, at no charge, with no paid tier, no paid
support, no advertising, no sponsorship and no donations
([CONTRIBUTING.md](CONTRIBUTING.md): *"No CLA, no donations, no sponsorships"*),
and is not bundled into any commercial offering. On that basis the Directive
does not apply to it.

That exclusion is a **condition, not a status**. It holds only while the project
stays non-commercial. Selling pre-loaded USB sticks, paid support, a paid hosted
variant, or a commercial fork supplied by the same maintainers would place the
supply in the course of a commercial activity and bring the project inside the
Directive, disclosure and presumption rules included. Any such step is a
decision to take legal advice on beforehand, not afterwards.

Where the Directive does not apply, ordinary national liability still can. In
Norway that is fault-based tort liability (culpa), which requires blameworthy
conduct by the maintainers and an adequate causal link to the harm — not merely
that a general-purpose tool existed and someone misused it.

## Facilitation, intent, and reports of misuse

Liability for another person's offence requires participation — in Norway,
*medvirkning* under straffeloven § 15 — and, for the offences relevant here,
intent. Negligence does not suffice. Distributing a general-purpose, dual-use
tool that has substantial lawful uses, without knowledge of any specific
unlawful use, and without any technical means of controlling what a user does
offline on their own device, is not participation.

There is no general obligation to monitor (Art. 8, Regulation (EU) 2022/2065;
Art. 15, Directive 2000/31/EC), and here there is nothing that *could* be
monitored: the project operates no server and receives no prompts, outputs or
identifiers. Equally, the hosting and caching safe harbours are not needed,
because the project hosts no user content.

Knowledge is the hinge. A credible report identifying a specific unlawful use is
the moment at which indifference could begin to look like intent. Reports are
received through the project's issue tracker at
<https://github.com/Codemaster64/onionmind/issues>; they are read, recorded with
a date, and answered. Where a report
identifies something the project can actually change — a link, a distribution
channel, a document, a default — it is changed, and the change is visible in the
public git history.

## Record of diligence

Model licences were verified against upstream repository metadata and recorded,
with dates, in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and in this file.
The decisions that bear on safety and legality — local-only processing, no
telemetry, a Tor preflight that refuses to run rather than leak, no monetisation
— are dated commits in the public git history. That history is the evidence that
these questions were addressed in advance rather than reconstructed afterwards.

## Anonymity tooling is lawful

Using, running and distributing Tor and related privacy tools is generally lawful
across the EU and EEA, including in Norway. This does not make unlawful uses
lawful. MAC randomization of one's own devices, RAM-only ("amnesic") operation,
and per-request circuit isolation are ordinary privacy measures.

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
content lies with the user. See [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md) and
[TERMS_OF_USE.md](TERMS_OF_USE.md).

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
