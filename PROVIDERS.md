# LLM providers

Humanizer Pro works without any API key: it then uses its offline rule engine. Add one or more keys
and it rewrites with an LLM instead, rotating across every configured provider and failing over
automatically when one is rate-limited, out of daily quota or down. If every provider fails, the
paragraph is still rewritten by the offline engine, so a job never stalls.

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
| `GROQ_API_KEY` | Groq | `llama-3.3-70b-versatile`, `llama-3.1-8b-instant` | yes | <https://console.groq.com/keys> |
| `GEMINI_API_KEY` | Google Gemini | `gemini-2.5-flash`, `gemini-2.5-flash-lite` | yes | <https://aistudio.google.com/apikey> |
| `CEREBRAS_API_KEY` | Cerebras | `llama-3.3-70b` | yes | <https://cloud.cerebras.ai> |
| `OPENROUTER_API_KEY` | OpenRouter | several `:free` models | yes | <https://openrouter.ai/keys> |
| `SAMBANOVA_API_KEY` | SambaNova | `Meta-Llama-3.3-70B-Instruct` | yes | <https://cloud.sambanova.ai> |
| `MISTRAL_API_KEY` | Mistral | `mistral-small-latest` | yes | <https://console.mistral.ai> |
| `GITHUB_MODELS_TOKEN` | GitHub Models | `gpt-4o-mini`, `Meta-Llama-3.3-70B-Instruct` | yes | fine-grained token with *models: read* |
| `TOGETHER_API_KEY` | Together | `Llama-3.3-70B-Instruct-Turbo` | trial credit | <https://api.together.ai> |
| `DEEPINFRA_API_KEY` | DeepInfra | `Llama-3.3-70B-Instruct` | trial credit | <https://deepinfra.com/dash> |
| `OPENAI_API_KEY` | OpenAI | `gpt-4o-mini` | paid | <https://platform.openai.com/api-keys> |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | `claude-sonnet-4-6` | paid | <https://console.anthropic.com/> |

Free tiers and model names change often; check each provider's page if a model stops working.
A model that returns *not found* is disabled for 12 hours and the pool moves on.

A `GITHUB_TOKEN` that is merely present in your shell or CI is deliberately **not** used, so your
documents are never sent to GitHub Models by accident. Use `GITHUB_MODELS_TOKEN`, or write
`GITHUB_TOKEN=...` into your `.env` file to opt in.

## Choose models

Every provider reads an optional `<NAME>_MODELS` variable (comma separated):

```dotenv
GROQ_MODELS=llama-3.3-70b-versatile
OPENROUTER_MODELS=meta-llama/llama-3.3-70b-instruct:free
GEMINI_MODELS=gemini-2.5-flash
ANTHROPIC_MODELS=claude-sonnet-4-6
GITHUB_MODELS=gpt-4o-mini
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
