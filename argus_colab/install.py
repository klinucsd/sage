"""ARGUS one-shot installer for Google Colab (and any IPython kernel).

Usage from inside a Colab notebook:

    # Cell 1 — declare your LLM provider (one of the templates from
    # argus_colab/README.md, or your own custom block).
    LLM_CONFIG = '''
    [models]
    default = "openai:gpt-4o-mini"

    [models.providers.openai]
    class_path = "langchain_openai:ChatOpenAI"
    models = ["gpt-4o-mini"]
    api_key_env = "OPENAI_API_KEY"
    '''

    # Cell 2 — universal install (same line for every provider).
    exec(__import__('urllib.request').urlopen(
        'https://raw.githubusercontent.com/klinucsd/sage/main/argus_colab/install.py'
    ).read().decode(), globals())

After cell 2 completes, %%ask, %%mcp, %%skill are all registered and ARGUS
is ready.

ARGUS does NOT enumerate or gatekeep LLM providers — your LLM_CONFIG
declares whatever you want, and the installer derives the API key env var
from your config. Works for OpenAI, Anthropic, NRP, ZAI, OpenRouter,
Nvidia, Mistral, Groq, or any other provider, present or future.

What the installer adds to your config automatically:
  - For every langchain_openai:ChatOpenAI provider WITHOUT a [params] block,
    we append sensible defaults: temperature=0, stream_chunk_timeout=1200,
    max_retries=6. These work better for agentic flows than ChatOpenAI's
    defaults. If you specify your own [models.providers.<name>.params]
    block, your values win — we don't override.
"""

import os
import pathlib
import subprocess
import sys
import textwrap
import urllib.request

try:
    import tomllib  # Python 3.11+ stdlib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

print("ARGUS bootstrap starting...")

# ---------------------------------------------------------------------------
# Step 1: Parse the user's LLM_CONFIG, augment with ChatOpenAI defaults,
# write to ~/.deepagents/config.toml
# ---------------------------------------------------------------------------
_deepagents_dir = pathlib.Path("~/.deepagents").expanduser()
_config_path = _deepagents_dir / "config.toml"
_deepagents_dir.mkdir(parents=True, exist_ok=True)
(_deepagents_dir / "agent" / "skills").mkdir(parents=True, exist_ok=True)

# Accept LLM_CONFIG (preferred) or fall back to ARGUS_CONFIG for backward compat.
_llm_config = globals().get("LLM_CONFIG") or globals().get("ARGUS_CONFIG")

if _llm_config:
    _user_toml = _llm_config.strip()
    try:
        _parsed = tomllib.loads(_user_toml)
    except Exception as e:
        raise RuntimeError(
            f"Could not parse LLM_CONFIG as TOML: {e}\n"
            f"Check that your config has [models] and [models.providers.<name>] "
            f"blocks. See argus_colab/README.md for working templates."
        ) from None

    # For every langchain_openai:ChatOpenAI provider that DOESN'T already declare
    # a [params] block, append one with ARGUS's preferred defaults. We append as
    # raw TOML text (not via a TOML serializer) so we don't need tomli-w
    # available before pip install runs in step 3.
    _default_chatopenai_params = textwrap.dedent("""
        temperature = 0
        stream_chunk_timeout = 1200.0
        max_retries = 6
    """).strip()

    _appended_params = []
    for _provider_name, _provider in (
        _parsed.get("models", {}).get("providers", {}).items()
    ):
        if (
            _provider.get("class_path") == "langchain_openai:ChatOpenAI"
            and "params" not in _provider
        ):
            _appended_params.append(
                f"\n\n[models.providers.{_provider_name}.params]\n"
                f"{_default_chatopenai_params}"
            )

    _config_content = _user_toml + "".join(_appended_params) + "\n"
    _config_path.write_text(_config_content)
    if _appended_params:
        print(
            f"  ✓ Wrote LLM config to {_config_path} "
            f"(added default params for {len(_appended_params)} ChatOpenAI provider(s))"
        )
    else:
        print(f"  ✓ Wrote LLM config to {_config_path}")
elif _config_path.exists():
    print(f"  ✓ Using existing config at {_config_path}")
else:
    raise RuntimeError(
        "No LLM_CONFIG provided and no existing ~/.deepagents/config.toml.\n\n"
        "Before running this installer, declare your LLM provider in a "
        "separate cell. Minimal OpenAI example:\n\n"
        "    LLM_CONFIG = '''\n"
        '    [models]\n'
        '    default = "openai:gpt-4o-mini"\n\n'
        "    [models.providers.openai]\n"
        '    class_path = "langchain_openai:ChatOpenAI"\n'
        '    models = ["gpt-4o-mini"]\n'
        '    api_key_env = "OPENAI_API_KEY"\n'
        "    '''\n\n"
        "See https://github.com/klinucsd/sage/blob/main/argus_colab/README.md "
        "for templates covering OpenAI, Anthropic, ZAI, OpenRouter, NRP, and "
        "other providers."
    )

# ---------------------------------------------------------------------------
# Step 2: Re-parse the final config (with appended defaults) to find
# api_key_env, then load the matching Colab Secret.
# ---------------------------------------------------------------------------
with open(_config_path, "rb") as _f:
    _config_parsed = tomllib.load(_f)

_default_model = _config_parsed.get("models", {}).get("default", "")
if ":" not in _default_model:
    raise RuntimeError(
        f"LLM_CONFIG must specify models.default = \"<provider>:<model>\"; "
        f"got {_default_model!r}"
    )
_provider_name = _default_model.split(":", 1)[0]
_provider_config = (
    _config_parsed.get("models", {})
    .get("providers", {})
    .get(_provider_name, {})
)
_api_key_env = _provider_config.get("api_key_env")
if not _api_key_env:
    raise RuntimeError(
        f"Provider {_provider_name!r} in LLM_CONFIG is missing the "
        f"api_key_env field. Add api_key_env = '<YOUR_KEY_ENV_VAR>' to that "
        f"provider's block."
    )

try:
    from google.colab import userdata  # type: ignore[import-not-found]

    _key = userdata.get(_api_key_env)
    if _key:
        os.environ[_api_key_env] = _key
        print(f"  ✓ Loaded {_api_key_env} from Colab Secrets")
    else:
        raise RuntimeError(
            f"Colab Secret {_api_key_env!r} is not set or empty.\n"
            f"Open the 🔑 sidebar in Colab → 'Add new secret' → "
            f"Name: {_api_key_env}, Value: your API key, toggle "
            f"'Notebook access' on, then re-run this cell."
        )
except ImportError:
    if _api_key_env not in os.environ:
        raise RuntimeError(
            f"{_api_key_env} not set in the environment. Outside Colab, "
            f"export {_api_key_env} before launching the kernel, or set "
            f"os.environ[{_api_key_env!r}] = '<your key>' in a separate "
            f"cell before this installer."
        )
    print(f"  ✓ Using {_api_key_env} from existing environment")

# ---------------------------------------------------------------------------
# Step 3: pip install ARGUS dependencies (skip if already present)
# ---------------------------------------------------------------------------
try:
    import deepagents_code  # noqa: F401

    print("  ✓ ARGUS dependencies already installed")
except ImportError:
    print("  · Installing ARGUS dependencies (~30–90 s the first time)...")
    subprocess.check_call(
        [
            sys.executable, "-m", "pip", "install", "-q",
            "deepagents-code==0.1.10",
            "langchain-mcp-adapters",
            "nest_asyncio",
            "folium", "geopandas", "ipyleaflet", "ipywidgets",
            "matplotlib", "rasterio", "leafmap", "plotly",
            "pypdf", "openpyxl", "tomli-w",
        ],
    )
    print("  ✓ ARGUS dependencies installed")

# ---------------------------------------------------------------------------
# Step 4: Download ARGUS source files
# ---------------------------------------------------------------------------
# Pull from 'main' during pre-1.0 active development; once a stable release
# tag is cut, this URL can be pinned to that tag for reproducibility.
_ARGUS_REF = "main"
_ARGUS_BASE = f"https://raw.githubusercontent.com/klinucsd/sage/{_ARGUS_REF}"
for _fname in ("sage_magic.py", "sage_kernel_backend.py"):
    urllib.request.urlretrieve(f"{_ARGUS_BASE}/{_fname}", f"/content/{_fname}")
if "/content" not in sys.path:
    sys.path.insert(0, "/content")
print(f"  ✓ Downloaded sage_magic.py + sage_kernel_backend.py from {_ARGUS_REF}")

# ---------------------------------------------------------------------------
# Step 5: Load ARGUS into the calling notebook's globals
# ---------------------------------------------------------------------------
# Use exec(..., globals()) so module-level names like SAGE_OUTPUT_DIR,
# SAGE_MESSAGES, _SAGE_MCP_TOOLS_BY_SERVER actually reach the user namespace.
# %run on Colab discards them; exec(..., globals()) doesn't.
exec(open("/content/sage_magic.py").read(), globals())

print("\nARGUS ready — %%ask, %%mcp, %%skill are registered.")
print(f"  Provider: {_provider_name}  ·  Model: {_default_model}")
