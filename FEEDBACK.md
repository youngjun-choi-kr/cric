# Feedback: Nebius Token Factory, Nemotron, Tavily

Written from building CRIC between 2026-10-24 and 2026-10-30. Every point below happened in this repo; commit
messages and LIMITATIONS.md have the details.

## What we used, and for what

| Tool | Model / API | Used for |
|---|---|---|
| Nebius Token Factory | OpenAI-compatible chat completions, `response_format: json_schema` | every model call |
| NVIDIA Nemotron 3 Super (120B-A12B) | `nvidia/nemotron-3-super-120b-a12b` | decisions, satellite dossiers, event records |
| NVIDIA Nemotron 3.5 Super VL | `nvidia/Nemotron-3.5-Super-VL` | reading SDO images of the Sun with NOAA data |
| NVIDIA Nemotron 3 Ultra (550B-A55B) | `nvidia/Nemotron-3-Ultra-550b-a55b` | coordination messages and escalation briefs, only when a person must act |
| NVIDIA Nemotron 3 Nano (30B-A3B) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | evaluated for decisions; not used in the final routing |
| Tavily | Search (advanced) + Extract (advanced, query-focused chunks) | satellite capability facts and real space-weather events, each with a verbatim quote |

## What worked well
- **Getting started was fast.** The OpenAI-compatible endpoint meant one `base_url` change; the first Nemotron
  call worked within minutes of getting a key. The model catalog lists IDs, context sizes and prices clearly.
- **`json_schema` structured output works on Super, Super VL and Ultra.** It removed most parsing problems after
  our first run returned the right content under different field names.
- **`reasoning_content` is returned separately.** We show it in the console as the decision trace; judges and
  operators can see why the model decided, not only what.
- **Nemotron Super reads plain-language constraints well.** On held-out scenarios where the deciding fact was only
  in free text (including notes written in Korean), Super was right 18 of 20 times (0.90) against 0.75 for a coded policy that cannot read them.
- **Super VL kept "observed" and "inferred" apart** and volunteered that one EUV image cannot confirm an
  Earth-directed CME, and that high-energy electron flux is a charging issue rather than drag.
- **Tavily Extract with a `query` made the difference** between snippets that miss the number and page text that
  contains it (for example the Kp 9 peak of the May 2024 storm on Wikipedia and NOAA pages).
  `exclude_domains` let us keep social media out of safety-relevant facts.

## What got in the way (with what we did about it)
1. **Reasoning tokens count against `max_tokens` and can exhaust it.** With `max_tokens=2000` the visible answer
   was cut at 821 characters. We now use 16k and treat `finish_reason == "length"` as a retryable error.
   *Suggestion:* a separate reasoning budget parameter, or documenting typical reasoning lengths per model.
2. **Guided decoding silently fills empty defaults when the model's planned JSON shape differs from the schema.**
   In one case the reasoning contained the correct value, URL and quote, but the constrained output was
   `status: "unknown"` everywhere; another time the title came back as "Placeholder Title". Adding an explicit
   JSON skeleton of the schema to the prompt fixed it (1/8 -> 8/8 cited facts, stable over repeats).
   *Suggestion:* document this, or have the server echo the schema to the model automatically.
3. **Occasional 503 responses for a minute or two.** Four retries over ~35 s were not always enough; we use five
   with up to 60 s backoff. *Suggestion:* a status page or `Retry-After` header.
4. **Nemotron fine-tuning is not available on Token Factory** (the post-training list has DeepSeek, Llama,
   gpt-oss, Qwen3). We had planned an Ultra-teaches-Nano distillation loop and dropped it.
   *Suggestion:* Nemotron Nano in serverless post-training would make the "small model for volume" story
   end-to-end on Nebius.
5. **Nano was slower than Super for this task** (median 30 s vs 12 s per decision) because it reasons longer, and
   4 of 39 Nano calls hit the 16k token limit. For short structured decisions Super was faster and cheaper per
   correct answer. *Suggestion:* a reasoning-effort control would let Nano be the fast tier it is meant to be.
6. **Rare malformed values inside valid JSON.** Once, text from the reasoning leaked into a string field
   (`"O1'} } // Note: ..."`). A `pattern` constraint on the field prevents it.

## Numbers from our runs
- A typical incident (dossiers cached): 1 Super decision + 1 Ultra report, about $0.011 and 15-25 s.
- Space-weather read with Super VL: about $0.004 and 10-20 s per image.
- A satellite dossier with Tavily: 3 searches + 1 extract (7-8 Tavily credits) and 1-2 Super calls.
- All evaluation runs together cost under $1 of Token Factory credit.

## Would we use it again?
Yes. The OpenAI-compatible API and clear pricing made it easy to route work by model size, and Super's handling of
free-text operational context is the part of CRIC that a rule engine could not replace. The two items we would most
like fixed are the reasoning budget (1) and the guided-decoding shape issue (2).
