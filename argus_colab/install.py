"""ARGUS one-shot installer for Google Colab (and any IPython kernel).

Usage from inside a Colab notebook:

    # Cell 1 — declare your LLM (simplest form: a Python dict)
    LLM = {
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        # "url": "https://api.z.ai/api/coding/paas/v4",  # optional, for
        #     OpenAI-compatible third-party hosts (ZAI, NRP, OpenRouter, Groq, ...)
        # "flavor": "openai" | "anthropic" | "gemini",   # optional, defaults to "openai"
    }

    # Cell 2 — universal install (same lines for every provider).
    import urllib.request
    exec(urllib.request.urlopen(
        'https://raw.githubusercontent.com/klinucsd/sage/main/argus_colab/install.py'
    ).read().decode(), globals())

After cell 2 completes, %%ask, %%mcp, %%skill are all registered and ARGUS
is ready.

ARGUS does NOT enumerate or gatekeep LLM providers — your LLM dict declares
whatever you want, and the installer derives the API key env var from it.
Works for OpenAI, Anthropic, Google Gemini, NRP, ZAI, OpenRouter, Nvidia,
Mistral, Groq, or any other provider, present or future.

For advanced setups (multi-provider configs, custom params, etc.), you can
also pass the full deepagents config as a TOML string in `LLM_CONFIG`. The
LLM dict and LLM_CONFIG paths produce equivalent on-disk config; the dict
is just the easy entry point.

What the installer adds to your config automatically:
  - For every langchain_openai:ChatOpenAI provider WITHOUT a [params] block,
    we append sensible defaults: temperature=0, stream_chunk_timeout=1200,
    max_retries=6. These work better for agentic flows than ChatOpenAI's
    defaults. If you specify your own [models.providers.<name>.params]
    block, your values win — we don't override.
"""

import json
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

# Core skills installed automatically on bootstrap (~/.deepagents/agent/skills/).
# Mirrors what the Docker image bakes in, minus:
#   - ndp-projects, ndp-workspaces (need Keycloak — only available on NDP JupyterHub)
#   - wildfire-* (private skills, not publicly distributed)
# Users can opt out by setting SKIP_CORE_SKILLS = True before running this installer.
_CORE_SKILLS = (
    "arcgis-feature-skill-builder",
    "ckan-skill-builder",
    "ndp-search",
    "repo-skill-builder",
    "sage-bbox-map",
    "sage-dropdown",
    "sage-metrics",
    "skillsmp",
    "us-counties",
    "us-states",
)

print("ARGUS bootstrap starting...")

# ---------------------------------------------------------------------------
# Step 1: Resolve the user's LLM config (dict OR TOML string), augment with
# ChatOpenAI defaults, write to ~/.deepagents/config.toml
# ---------------------------------------------------------------------------
_deepagents_dir = pathlib.Path("~/.deepagents").expanduser()
_config_path = _deepagents_dir / "config.toml"
_deepagents_dir.mkdir(parents=True, exist_ok=True)
(_deepagents_dir / "agent" / "skills").mkdir(parents=True, exist_ok=True)

# Three accepted user-facing shapes, in priority order:
#   1. LLM = {"model": "...", "url": "...", "api_key_env": "...",
#             "flavor": "openai" | "anthropic" | "gemini"}  (preferred — simplest)
#   2. LLM_CONFIG = '''<TOML string>'''  (advanced — full deepagents config)
#   3. ARGUS_CONFIG = '''<TOML string>'''  (deprecated alias for LLM_CONFIG)
_FLAVOR_TO_CLASS_PATH = {
    "openai": "langchain_openai:ChatOpenAI",
    "anthropic": "langchain_anthropic:ChatAnthropic",
    "gemini": "langchain_google_genai:ChatGoogleGenerativeAI",
}


def _llm_dict_to_toml(d):
    """Translate the simple LLM dict into a deepagents config.toml fragment.

    Required keys:
      - model       (str) the model id, e.g. "gpt-4o-mini", "glm-5"
      - api_key_env (str) the env var holding the API key, e.g. "OPENAI_API_KEY"

    Optional keys:
      - url         (str) the API base URL. Omit to use the langchain default
                          for the chosen flavor (api.openai.com, api.anthropic.com,
                          etc.). Set this for OpenAI-compatible third-party hosts
                          like ZAI, NRP, OpenRouter, Groq, Together, etc.
      - flavor      (str) one of "openai", "anthropic", "gemini". Defaults to
                          "openai", which works for OpenAI itself plus any
                          OpenAI-compatible endpoint.
    """
    if not isinstance(d, dict):
        raise RuntimeError(f"LLM must be a dict, got {type(d).__name__}")
    flavor = d.get("flavor", "openai")
    if flavor not in _FLAVOR_TO_CLASS_PATH:
        raise RuntimeError(
            f"LLM['flavor'] must be one of {sorted(_FLAVOR_TO_CLASS_PATH)}; "
            f"got {flavor!r}"
        )
    model = d.get("model")
    if not isinstance(model, str) or not model:
        raise RuntimeError(
            "LLM dict must include 'model' (e.g., 'gpt-4o-mini', 'glm-5')"
        )
    api_key_env = d.get("api_key_env")
    if not isinstance(api_key_env, str) or not api_key_env:
        raise RuntimeError(
            "LLM dict must include 'api_key_env' (e.g., 'OPENAI_API_KEY', "
            "'ZAI_API_KEY'). This names the Colab Secret holding your key."
        )
    url = d.get("url")
    class_path = _FLAVOR_TO_CLASS_PATH[flavor]

    # Provider name in the generated TOML is mostly a label, BUT — for the
    # 'openai' flavor — using the literal name "openai" makes langchain-openai
    # route through the OpenAI Responses API (api.openai.com/v1/responses).
    # That's correct for actual OpenAI, but breaks third-party OpenAI-compatible
    # endpoints (ZAI, NRP, OpenRouter, Groq, etc.) which only support the
    # chat-completions path. Heuristic: if the user gave us a custom URL,
    # assume third-party compat endpoint and use a neutral provider name.
    if flavor == "openai" and isinstance(url, str) and url:
        provider_name = "llm"
    else:
        provider_name = flavor

    lines = [
        "[models]",
        f'default = "{provider_name}:{model}"',
        "",
        f"[models.providers.{provider_name}]",
        f'class_path = "{class_path}"',
        f'models = ["{model}"]',
        f'api_key_env = "{api_key_env}"',
    ]
    if isinstance(url, str) and url:
        lines.append(f'base_url = "{url}"')
    return "\n".join(lines)


_llm_dict = globals().get("LLM")
_llm_toml_str = globals().get("LLM_CONFIG") or globals().get("ARGUS_CONFIG")

if _llm_dict is not None:
    _user_toml = _llm_dict_to_toml(_llm_dict).strip()
    _llm_config = _user_toml  # nonempty marker for the if-tree below
    _source_label = "LLM dict"
elif _llm_toml_str:
    _user_toml = _llm_toml_str.strip()
    _llm_config = _user_toml
    _source_label = "LLM_CONFIG TOML"
else:
    _llm_config = None
    _source_label = None

if _llm_config:
    try:
        _parsed = tomllib.loads(_user_toml)
    except Exception as e:
        raise RuntimeError(
            f"Could not parse {_source_label} as TOML: {e}\n"
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
        "No LLM config provided, and no existing ~/.deepagents/config.toml.\n\n"
        "Before running this installer, declare your LLM in a separate cell.\n"
        "Simplest form (Python dict):\n\n"
        "    LLM = {\n"
        '        "model": "gpt-4o-mini",\n'
        '        "api_key_env": "OPENAI_API_KEY",\n'
        "    }\n\n"
        "For OpenAI-compatible third-party endpoints (ZAI, NRP, OpenRouter, "
        "Groq, etc.), also include the 'url':\n\n"
        "    LLM = {\n"
        '        "model": "glm-5",\n'
        '        "url": "https://api.z.ai/api/coding/paas/v4",\n'
        '        "api_key_env": "ZAI_API_KEY",\n'
        "    }\n\n"
        "See https://github.com/klinucsd/sage/blob/main/argus_colab/README.md "
        "for templates covering OpenAI, Anthropic, ZAI, OpenRouter, NRP, Gemini, "
        "and other providers."
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
# Step 4b: Install core skills into ~/.deepagents/agent/skills/
# ---------------------------------------------------------------------------
# Mirrors what the Docker image bakes in by default. Skipped per-skill if the
# user already has it (e.g. via a prior %%skill cell). User can disable
# entirely with SKIP_CORE_SKILLS = True before running this installer.
if globals().get("SKIP_CORE_SKILLS"):
    print("  · Skipping core-skill install (SKIP_CORE_SKILLS=True)")
else:
    _skills_dir = pathlib.Path("~/.deepagents/agent/skills").expanduser()
    _skills_dir.mkdir(parents=True, exist_ok=True)
    # One GitHub tree call enumerates every file in the sage repo at this ref —
    # avoids N per-skill API calls and stays well under the unauth rate limit.
    _tree_url = (
        f"https://api.github.com/repos/klinucsd/sage/git/trees/{_ARGUS_REF}"
        f"?recursive=1"
    )
    try:
        with urllib.request.urlopen(_tree_url, timeout=15) as _resp:
            _tree = json.loads(_resp.read().decode()).get("tree", [])
    except Exception as _e:
        print(f"  ⚠ Could not list sage repo tree for core skills: {_e}")
        _tree = []

    _installed_core = []
    _skipped_core = []
    for _skill_name in _CORE_SKILLS:
        _skill_dir = _skills_dir / _skill_name
        if _skill_dir.exists():
            _skipped_core.append(_skill_name)
            continue
        _prefix = f"sage_skills/{_skill_name}/"
        _files_for_skill = [
            e for e in _tree
            if e.get("type") == "blob" and e.get("path", "").startswith(_prefix)
        ]
        if not _files_for_skill:
            continue
        for _entry in _files_for_skill:
            _rel = _entry["path"][len(_prefix):]
            _local = _skill_dir / _rel
            _local.parent.mkdir(parents=True, exist_ok=True)
            _raw_url = (
                f"https://raw.githubusercontent.com/klinucsd/sage/"
                f"{_ARGUS_REF}/{_entry['path']}"
            )
            urllib.request.urlretrieve(_raw_url, str(_local))
        _installed_core.append(_skill_name)
    if _installed_core:
        print(
            f"  ✓ Installed {len(_installed_core)} core skill(s) into "
            f"{_skills_dir}: {', '.join(_installed_core)}"
        )
    if _skipped_core:
        print(
            f"  · Skipped {len(_skipped_core)} core skill(s) (already present): "
            f"{', '.join(_skipped_core)}"
        )

# ---------------------------------------------------------------------------
# Step 5: Load ARGUS into the calling notebook's globals
# ---------------------------------------------------------------------------
# Use exec(..., globals()) so module-level names like SAGE_OUTPUT_DIR,
# SAGE_MESSAGES, _SAGE_MCP_TOOLS_BY_SERVER actually reach the user namespace.
# %run on Colab discards them; exec(..., globals()) doesn't.
exec(open("/content/sage_magic.py").read(), globals())

print("\nARGUS ready — %%ask, %%mcp, %%skill are registered.")
print(f"  Provider: {_provider_name}  ·  Model: {_default_model}")
