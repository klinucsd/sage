# Running ARGUS on Google Colab

ARGUS runs end-to-end on Google Colab. Anyone with a Google account and an LLM API key can open an ARGUS notebook, install ARGUS in two cells, and start asking natural-language questions.

## Quick start (two cells)

Open a Colab notebook. The first cell declares your LLM provider; the second cell installs ARGUS.

### Cell 1 — choose your LLM provider

Pick one of the templates from **[Provider templates](#provider-templates)** below and paste it into the first cell. Edit the model name if you want a different model from the same provider.

### Cell 2 — universal install

Paste this exact line in the second cell. It's identical for every provider:

```python
exec(__import__('urllib.request').urlopen(
    'https://raw.githubusercontent.com/klinucsd/sage/main/argus_colab/install.py'
).read().decode(), globals())
```

### Cell 3 onward — your usual `%%ask`, `%%mcp`, `%%skill` cells

Once cells 1–2 finish, ARGUS is ready. Write a `%%ask` cell to start asking questions.

### Add your API key as a Colab Secret

The installer reads your API key from Colab Secrets. The Secret name must match the `api_key_env` field in your config (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`).

1. Click the 🔑 icon in the left sidebar of Colab
2. Click "Add new secret"
3. Name: the `api_key_env` value from your template (e.g. `OPENAI_API_KEY`)
4. Value: your API key
5. Toggle "Notebook access" on

The installer also gives a clear error message if the Secret is missing or empty, so you can fix it and re-run.

## Provider templates

Copy ONE of these into your first cell. ARGUS doesn't gatekeep providers — if you have one that isn't listed below, look at your provider's docs and adapt one of the templates (typically just change `base_url`, `api_key_env`, and `models`).

### OpenAI

```python
LLM_CONFIG = '''
[models]
default = "openai:gpt-4o-mini"

[models.providers.openai]
class_path = "langchain_openai:ChatOpenAI"
models = ["gpt-4o-mini", "gpt-4o"]
api_key_env = "OPENAI_API_KEY"
'''
```

### Anthropic Claude

```python
LLM_CONFIG = '''
[models]
default = "anthropic:claude-3-5-sonnet-latest"

[models.providers.anthropic]
class_path = "langchain_anthropic:ChatAnthropic"
models = ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"]
api_key_env = "ANTHROPIC_API_KEY"
'''
```

### ZAI (Zhipu AI hosted GLM-5)

Cheaper than OpenAI for dev; OpenAI-compatible endpoint.

```python
LLM_CONFIG = '''
[models]
default = "nrp:glm-5"

[models.providers.nrp]
class_path = "langchain_openai:ChatOpenAI"
models = ["glm-5"]
api_key_env = "ZAI_API_KEY"
base_url = "https://api.z.ai/api/coding/paas/v4"
'''
```

### NRP (National Research Platform)

For users with an NRP account.

```python
LLM_CONFIG = '''
[models]
default = "nrp:glm-5"

[models.providers.nrp]
class_path = "langchain_openai:ChatOpenAI"
models = ["glm-5"]
api_key_env = "NRP_API_KEY"
base_url = "https://ellm.nrp-nautilus.io/v1"
'''
```

### OpenRouter

Single key for hundreds of models from many providers (OpenAI, Anthropic, Mistral, etc.).

```python
LLM_CONFIG = '''
[models]
default = "openrouter:openai/gpt-4o-mini"

[models.providers.openrouter]
class_path = "langchain_openai:ChatOpenAI"
models = ["openai/gpt-4o-mini", "anthropic/claude-3-5-sonnet", "mistralai/mistral-large-latest"]
api_key_env = "OPENROUTER_API_KEY"
base_url = "https://openrouter.ai/api/v1"
'''
```

### Other OpenAI-compatible endpoints (Groq, Together, Fireworks, Nvidia, etc.)

Most modern LLM hosts offer an OpenAI-compatible API. Start from the OpenAI template above and swap three fields:

```python
LLM_CONFIG = '''
[models]
default = "my_provider:<their model id>"

[models.providers.my_provider]
class_path = "langchain_openai:ChatOpenAI"
models = ["<their model id>"]
api_key_env = "MY_PROVIDER_KEY"        # whatever you name your Colab Secret
base_url = "https://<their api base>"  # from their docs
'''
```

## What the installer does

The `install.py` linked above performs five steps when you run it:

1. Writes your `LLM_CONFIG` to `~/.deepagents/config.toml` (or uses an existing one if you didn't declare `LLM_CONFIG` in cell 1). For every `langchain_openai:ChatOpenAI` provider without its own `[params]` block, the installer appends sensible defaults: `temperature = 0`, `stream_chunk_timeout = 1200.0`, `max_retries = 6`. These work better for agentic flows than the library defaults. If you write your own `[params]` block, the installer leaves it alone.
2. Parses the config to find which `api_key_env` your provider expects
3. Loads that Colab Secret into `os.environ`
4. Installs ARGUS's Python dependencies via pip
5. Downloads `sage_magic.py` from this repo's `main` branch and registers the `%%ask` / `%%mcp` / `%%skill` magics into the running kernel

If anything fails, the installer prints a clear, specific error message naming exactly what's wrong (missing key, missing config, missing field, etc.).

## Troubleshooting

**"No LLM_CONFIG provided ..."** — you ran the install cell without first running a cell that defines `LLM_CONFIG`. Pick a provider template above, put it in a cell, and run it before the install cell.

**"Colab Secret 'XXX' is not set or empty"** — go to the 🔑 sidebar in Colab and add the Secret with that exact name. Make sure "Notebook access" is toggled on.

**`%%ask` returns no output** — see the "If the smoke test fails" section in `bootstrap_test.ipynb`, or check `SAGE_MESSAGES[-1]` and `_SAGE_LAST_RUN_CHUNKS` for diagnostic details.

**Other** — open an issue at https://github.com/klinucsd/sage/issues with the cell output and your `ARGUS_CONFIG` (with your API key redacted).

## Pinning to a release

The installer pulls from the `main` branch by default. Once we cut a formal release tag (e.g. `v1.4.3`), you can pin by editing the install line:

```python
exec(__import__('urllib.request').urlopen(
    'https://raw.githubusercontent.com/klinucsd/sage/v1.4.3/argus_colab/install.py'
).read().decode(), globals())
```

For now, `main` is the right choice — we're actively releasing bugfixes and the bootstrap experience is still maturing.
