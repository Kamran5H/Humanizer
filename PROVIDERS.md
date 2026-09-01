# Free LLM Provider Pool — how to escape rate limits

Groq's free tier caps at 100k tokens/day per model. Once you hit it, you're
stuck on the local fallback for the rest of the day.

Humanizer Pro now auto-discovers **any** provider whose key you drop into
`.keys.env` and merges them all into one failover pool. Add a key → the pool
uses it. Nothing else to configure.

## The order the pool tries endpoints

1. `STEALTH_API_BASE` (legacy — usually Groq)
2. Cerebras
3. SambaNova
4. OpenRouter (the `:free` models require zero credit)
5. GitHub Models
6. Together
7. Mistral
8. DeepInfra
9. `STEALTH_FALLBACK_BASE` (local Ollama)

If Groq is TPD-exhausted, the very next call goes to Cerebras (or whichever
provider you added), zero delay. Once every cloud provider is cooling,
Ollama picks up. When Groq's quota resets, it reclaims the top slot.

## Sign up + add the key

Copy any of these lines into `C:\Users\chkam\OneDrive\Desktop\BrandFinder\.keys.env`,
replace the placeholder with your key. Restart the app.

```dotenv
# Cerebras — llama-3.3-70b at ~2000 tok/s, ~1M tokens/day free
# Signup: https://cloud.cerebras.ai   (click "API Keys")
CEREBRAS_API_KEY=csk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# SambaNova — Meta-Llama-3.3-70B, generous daily quota
# Signup: https://cloud.sambanova.ai
SAMBANOVA_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# OpenRouter — one key unlocks many :free models (llama, deepseek, qwen, mistral)
# Signup: https://openrouter.ai/keys  (no credit card required)
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# GitHub Models — GPT-4o-mini and Llama-3.3-70B free via your existing GitHub PAT
# Create a fine-grained token: https://github.com/settings/tokens?type=beta
# It only needs the "models: read" permission.
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Together AI — free Llama-3.1-70B-Turbo
# Signup: https://api.together.ai
TOGETHER_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Mistral La Plateforme — free experimental tier
# Signup: https://console.mistral.ai
MISTRAL_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# DeepInfra — small free credit at signup
# Signup: https://deepinfra.com/dash
DEEPINFRA_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## Override the model list per provider

Each `_API_KEY` env var has a matching `_MODELS` env var. For example:

```dotenv
# Only use Cerebras's llama-3.3-70b (skip qwen)
CEREBRAS_MODELS=llama-3.3-70b

# Only use OpenRouter's DeepSeek free model
OPENROUTER_MODELS=deepseek/deepseek-chat-v3.1:free
```

## Recommendation

At minimum, add **Cerebras + OpenRouter** — together they give you effectively
unlimited daily throughput on 70B-class models for free, and the pool will
transparently balance across them when Groq's daily cap hits.
