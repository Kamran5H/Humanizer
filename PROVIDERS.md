# LLM providers

Humanizer Pro works without any API key: it then uses its offline rule engine. Add one or more keys
and it rewrites with an LLM instead, rotating across every configured provider and failing over
automatically when one is rate-limited, out of daily quota or down. Fallback to
offline rules is available to legacy clients; the interactive apps require AI
in AI mode and report a failure if no safe AI rewrite is available.

The browser Studio's **AI** mode requires an AI response and does **not** silently
switch to offline rules. Provider errors or rejected rewrites fail the job with
an explanation. Select **Offline** explicitly to use the local rule engine.
Legacy API/CLI clients retain fallback behavior unless they set
`allow_offline_fallback=False` in `HumanizerConfig` (or `false` in a job request).

ChatGPT, Claude, Claude Code, Codex, and other app subscriptions do not configure
this app's API connection. A supported provider's API credentials and available
quota are required. A provider shown as **configured** has not yet been tested
with a live request.

## Add a key

Copy `.env.example` to `.env` (or `.keys.env`) in the project folder, or in `~/.config/humanizer/`,
and fill in any keys you have. Restart the app. Environment variables work too.

| Variable | Provider | Default model(s) | Free tier | Sign up |
|---|---|---|---|---|
| `GROQ_API_KEY` | Groq | `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` | yes | <https://console.groq.com/keys> |
| `GEMINI_API_KEY` | Google Gemini | `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` | yes | <https://aistudio.google.com/apikey> |
| `CEREBRAS_API_KEY` | Cerebras | `gpt-oss-120b` | yes | <https://cloud.cerebras.ai> |
| `OPENROUTER_API_KEY` | OpenRouter | `openrouter/free` | yes | <https://openrouter.ai/keys> |
| `SAMBANOVA_API_KEY` | SambaNova | `Meta-Llama-3.3-70B-Instruct` | yes | <https://cloud.sambanova.ai> |
| `MISTRAL_API_KEY` | Mistral | `mistral-small-latest` | yes | <https://console.mistral.ai> |
| `TOGETHER_API_KEY` | Together | `Llama-3.3-70B-Instruct-Turbo` | trial credit | <https://api.together.ai> |
| `DEEPINFRA_API_KEY` | DeepInfra | `Llama-3.3-70B-Instruct` | trial credit | <https://deepinfra.com/dash> |
| `OPENAI_API_KEY` | OpenAI | `gpt-4o-mini` | paid | <https://platform.openai.com/api-keys> |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | `claude-sonnet-4-6` | paid | <https://console.anthropic.com/> |

Free tiers and model names change often; check each provider's page if a model stops working.
A model that returns *not found* is disabled for 12 hours and the pool moves on.

GitHub Models [retired on July 30, 2026](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/). Its former tokens are ignored and a configuration warning is shown. Ambient GitHub tokens are never used for inference. Cerebras defaults to `gpt-oss-120b` from its [current catalog](https://inference-docs.cerebras.ai/models/overview); OpenRouter uses its [free router](https://openrouter.ai/openrouter/free) rather than a hard-coded list of aging free model IDs.

## Choose models

Every provider reads an optional `<NAME>_MODELS` variable (comma separated):

```dotenv
GROQ_MODELS=openai/gpt-oss-120b,qwen/qwen3.8-27b
OPENROUTER_MODELS=openrouter/free
GEMINI_MODELS=gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite
ANTHROPIC_MODELS=claude-sonnet-4-6
```

## Custom and local endpoints

Any OpenAI-compatible server can be added; it is tried first:

```dotenv
STEALTH_API_BASE=https://my-endpoint.example.com/v1
STEALTH_API_KEY=...
STEALTH_MODEL=my-model
```

A local [Ollama](https://ollama.com) server is used last, after every cloud provider:

```dotenv
STEALTH_FALLBACK_BASE=http://localhost:11434/v1
STEALTH_FALLBACK_MODEL=qwen2.5:7b
```

## How failover works

| Event | What happens |
|---|---|
| 429 rate limit | endpoint cools for the `Retry-After` time (default 30 s) |
| Daily quota exhausted | endpoint cools for 6 h |
| 401/403 bad key | endpoint cools for 6 h; the reason is shown in the studio's provider list |
| 404 unknown model | endpoint cools for 12 h |
| 400/413/422 rejected request | endpoint cools for 5 min |
| 5xx / network error | endpoint cools for 45–60 s |

Cooldowns persist across restarts. Calls to the same host are spaced 1.5 s apart (set
`HUMANIZER_MIN_INTERVAL` to change this) without blocking calls to other providers. Click the engine
pill in the studio header to see each endpoint's live status and reset all cooldowns.

Claude uses Anthropic's native `/v1/messages` endpoint, with `x-api-key` and
`anthropic-version` headers. Blank or truncated responses are rejected rather
than used as a finished rewrite. Set `ANTHROPIC_MODELS` to a model available to
your account if the default is unavailable. Sampling parameters are omitted for
compatibility with newer Claude models, which do not support them; see the
[Messages API guide](https://platform.claude.com/docs/en/build-with-claude/working-with-messages).

## OmniRoute and FreeLLMAPI gateways

[OmniRoute](https://github.com/cheaper-inference/OmniRoute) and
[FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) expose OpenAI-compatible
endpoints and manage their own upstream credentials and routing. Humanizer can
rotate across both gateways, their configured models, and direct providers.
Configured gateways are preferred before direct providers; Ollama is last.

```dotenv
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
OMNIROUTE_API_KEY=your-gateway-key
OMNIROUTE_MODELS=auto/best-free,auto/best-chat,groq/openai/gpt-oss-120b,gemini/gemini-3.5-flash,openrouter/auto

FREELLMAPI_BASE_URL=http://127.0.0.1:3001/v1
FREELLMAPI_API_KEY=your-unified-key
FREELLMAPI_MODELS=auto:reliable
```

If `OMNIROUTE_MODELS` is omitted, Humanizer defaults to `auto/best-free,auto/best-chat`.
You can also specify exact OmniRoute model or combo IDs from its dashboard.
FreeLLMAPI documents `auto` and `auto:reliable` routing aliases. Its gateway
key does not create quota: upstream provider accounts must be configured there.
`localhost` refers to the machine running Humanizer. If the gateway runs on
Google Colab (via [`OmniRoute_Colab.ipynb`](OmniRoute_Colab.ipynb)) or another
machine, run the notebook's Cloudflare Tunnel cell (`Step 4B`) and set
`OMNIROUTE_BASE_URL=https://<tunnel-id>.trycloudflare.com/v1`.

Open the provider panel and click **Test connections** to send a tiny test prompt
to each endpoint. Successful checks show **connection tested**; a failed or
rate-limited endpoint shows its cooldown and reason. No document is used for a
connection test. Keys remain on the server.

## Free option without an API key

OVHcloud documents an [anonymous AI Endpoints tier](https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-endpoints-capabilities)
limited to two requests/minute per model. Enable it explicitly:

```dotenv
HUMANIZER_ENABLE_OVH_ANONYMOUS=1
OVH_MODELS=Meta-Llama-3_3-70B-Instruct,Mistral-Small-3.2-24B-Instruct-2506
```

This sends rewrite text to OVHcloud. Requests are paced separately per model,
without an authorization header; failures join the normal failover/cooldown
mechanism. Both models answered live requests during implementation. Free access
and model availability can change, and the anonymous limits make long documents
slower than a configured gateway or account-backed API.
