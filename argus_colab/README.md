# Running ARGUS on Google Colab

ARGUS runs end-to-end on Google Colab. Anyone with a Google account and an LLM API key can open an ARGUS notebook, install ARGUS in two cells, and start asking natural-language questions.

## Quick start (two cells)

Open a Colab notebook. The first cell declares your LLM; the second cell installs ARGUS.

### Cell 1 — declare your LLM (Python dict, 2–4 fields)

Pick one of the templates from **[Provider templates](#provider-templates)** below and paste it into the first cell.

### Cell 2 — universal install

Paste this exact line in the second cell. It's identical for every provider:

```python
import urllib.request
exec(urllib.request.urlopen(
    'https://raw.githubusercontent.com/klinucsd/sage/main/argus_colab/install.py'
).read().decode(), globals())
```

### Cell 3 onward — your usual `%%ask`, `%%mcp`, `%%skill` cells

Once cells 1–2 finish, ARGUS is ready. Write a `%%ask` cell to start asking questions.

### Add your API key as a Colab Secret

The installer reads your API key from Colab Secrets. The Secret name must match the `api_key_env` field in your `LLM` dict.

1. Click the 🔑 icon in the left sidebar of Colab
2. Click "Add new secret"
3. Name: the `api_key_env` value from your template (e.g. `OPENAI_API_KEY`, `ZAI_API_KEY`, `ANTHROPIC_API_KEY`)
4. Value: your API key
5. Toggle "Notebook access" on

The installer also prints a clear error message if the Secret is missing or empty.

## Provider templates

Copy ONE of these into your first cell. ARGUS doesn't gatekeep providers — any provider with an OpenAI-, Anthropic-, or Gemini-compatible API works. If you're using a third-party host (Groq, Together, Nvidia NIM, etc.), see the "Other OpenAI-compatible endpoints" section below.

### OpenAI

```python
LLM = {
    "model": "gpt-4o-mini",
    "api_key_env": "OPENAI_API_KEY",
}
```

### Anthropic Claude

```python
LLM = {
    "model": "claude-3-5-sonnet-latest",
    "api_key_env": "ANTHROPIC_API_KEY",
    "flavor": "anthropic",
}
```

### Google Gemini

```python
LLM = {
    "model": "gemini-1.5-pro",
    "api_key_env": "GOOGLE_API_KEY",
    "flavor": "gemini",
}
```

### ZAI (Zhipu AI hosted GLM-5)

Cheaper than OpenAI for dev; OpenAI-compatible endpoint.

```python
LLM = {
    "model": "glm-5",
    "url": "https://api.z.ai/api/coding/paas/v4",
    "api_key_env": "ZAI_API_KEY",
}
```

### NRP (National Research Platform)

For users with an NRP account.

```python
LLM = {
    "model": "glm-5",
    "url": "https://ellm.nrp-nautilus.io/v1",
    "api_key_env": "NRP_API_KEY",
}
```

### OpenRouter

Single key for hundreds of models from many providers (OpenAI, Anthropic, Mistral, etc.).

```python
LLM = {
    "model": "openai/gpt-4o-mini",
    "url": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY",
}
```

### Other OpenAI-compatible endpoints (Groq, Together, Fireworks, Nvidia NIM, etc.)

Most modern LLM hosts offer an OpenAI-compatible API. Use the ZAI/NRP/OpenRouter shape — pass `model`, `url`, and `api_key_env`. The default flavor is `"openai"`, which routes through langchain's `ChatOpenAI` client and works for any OpenAI-compatible endpoint.

```python
LLM = {
    "model": "<their model id>",
    "url": "https://<their api base>",       # from their docs
    "api_key_env": "MY_PROVIDER_KEY",        # any env var name you like for your Colab Secret
}
```

### Advanced: full TOML config (multi-provider, custom params, etc.)

If you need a setup the dict shape can't express (multiple providers in one config, custom langchain params, etc.), pass the full deepagents config as a TOML string in `LLM_CONFIG` instead of using the `LLM` dict:

```python
LLM_CONFIG = '''
[models]
default = "openai:gpt-4o-mini"

[models.providers.openai]
class_path = "langchain_openai:ChatOpenAI"
models = ["gpt-4o-mini", "gpt-4o"]
api_key_env = "OPENAI_API_KEY"

[models.providers.openai.params]
temperature = 0.3
max_retries = 10
'''
```

## What the installer does

The `install.py` linked above performs six steps when you run it:

1. Reads your `LLM` dict (or `LLM_CONFIG` TOML string) and writes the result to `~/.deepagents/config.toml`. For every `langchain_openai:ChatOpenAI` provider without its own `[params]` block, the installer appends sensible defaults: `temperature = 0`, `stream_chunk_timeout = 1200.0`, `max_retries = 6`. These work better for agentic flows than langchain's defaults. If you write your own `[params]` block, the installer leaves it alone.
2. Parses the config to find which `api_key_env` your provider expects
3. Loads that Colab Secret into `os.environ`
4. Installs ARGUS's Python dependencies via pip
5. Downloads `sage_magic.py` from this repo's `main` branch
6. Installs the **core skills** (`ndp-search`, `sage-bbox-map`, `sage-dropdown`, `sage-metrics`, `skillsmp`, `us-counties`, `us-states`) into `~/.deepagents/agent/skills/`. These mirror what the Docker image bakes in for JupyterHub users, minus the NDP-JupyterHub-only ones (`ndp-projects`, `ndp-workspaces`) which require a Keycloak token that's not available outside NDP. Skip per-skill if you already installed it via an earlier `%%skill` cell. To skip ALL core-skill install, set `SKIP_CORE_SKILLS = True` before running the installer.
7. Registers the `%%ask` / `%%mcp` / `%%skill` magics into the running kernel.

If anything fails, the installer prints a clear, specific error message naming exactly what's wrong (missing key, missing config, missing field, etc.).

## Troubleshooting

**"No LLM config provided ..."** — you ran the install cell without first running a cell that defines `LLM` (the dict) or `LLM_CONFIG` (the TOML string). Pick a template above, put it in a cell, and run it before the install cell.

**"Colab Secret 'XXX' is not set or empty"** — go to the 🔑 sidebar in Colab and add the Secret with that exact name. Make sure "Notebook access" is toggled on.

**`%%ask` returns no output** — see the "If the smoke test fails" section in `bootstrap_test.ipynb`, or check `SAGE_MESSAGES[-1]` and `_SAGE_LAST_RUN_CHUNKS` for diagnostic details.

**Other** — open an issue at https://github.com/klinucsd/sage/issues with the cell output and your `LLM` config (with your API key redacted).

## Pinning to a release

The installer pulls from the `main` branch by default. Once we cut a formal release tag (e.g. `v1.4.3`), you can pin by editing the install line:

```python
import urllib.request
exec(urllib.request.urlopen(
    'https://raw.githubusercontent.com/klinucsd/sage/v1.4.3/argus_colab/install.py'
).read().decode(), globals())
```

For now, `main` is the right choice — we're actively releasing bugfixes and the bootstrap experience is still maturing.
