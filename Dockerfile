# =============================================================================
# ARGUS Docker Image - NRP JupyterHub Deployment
# =============================================================================
#
# Build:
#   docker build -t argus:jupyterhub .
#
# Tag and push (dev iterations):
#   docker tag argus:jupyterhub kaiucsd/argus-dev:v1.4.3
#   docker push kaiucsd/argus-dev:v1.4.3
#
# Formal release:
#   docker tag argus:jupyterhub kaiucsd/argus:v1.4
#   docker push kaiucsd/argus:v1.4
#
# NRP_API_KEY is loaded at runtime from:
#   1. /home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env
#   2. .env in the current working directory
#   3. Environment variable already set
#
# Image Details:
#   - Base: jupyter/base-notebook (Python 3, JupyterLab, jovyan user pre-configured)
#   - User: jovyan (UID 1000)
#   - Home: /home/jovyan/work
#   - Port: 8888
#   - Skills: ndp-search, us-states, us-counties, usgs-earthquake-events,
#             ndp-workspaces, kanawha-flood-depth, kanawha-reach-impact,
#             kanawha-cikr-impact, kanawha-nsi-impact
# =============================================================================

FROM quay.io/jupyter/base-notebook:latest

# -----------------------------------------------------------------------------
# Step 0: Install Common System Tools
# -----------------------------------------------------------------------------
USER root
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git curl wget zip unzip vim \
    openssh-client rsync ripgrep \
    less tree htop nano jq \
    build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Step 1: Install DeepAgents CLI
# -----------------------------------------------------------------------------
# JupyterLab is pinned to 4.2.4 to match NRP JupyterHub's runtime. Without
# this pin, the image ships JupyterLab 4.5.5 but NRP downgrades jupyterlab at
# pod startup, leaving the prebuilt labextensions (e.g. the ipywidgets manager)
# compiled against the wrong API version — widgets then render as plain text.
# Pinning here forces pip to install labextension versions compatible with 4.2.4.
#
# deepagents-code is the interactive coding agent package (renamed from
# deepagents-cli starting at 0.2.x; the legacy deepagents-cli 0.2.x is now
# deployment tooling only). 0.1.84 brings deepagents==0.7.24, langchain-openai,
# langchain-anthropic, fastmcp, and the LangGraph runtime as default deps —
# no extras needed.
#
# Upgraded from 0.1.54 (deepagents 0.7.5) on 2026-10-13. All 15 ARGUS bindings
# were re-probed inside the built image against 0.1.84/0.7.24 before the bump:
# create_deep_agent still accepts system_prompt/backend/tools/middleware/
# checkpointer/skills; LocalShellBackend.execute is still (command, *,
# timeout=None); backends.protocol.ExecuteResponse, middleware.filesystem
# .execute_accepts_timeout, deepagents_code.config.create_model and
# .detect_provider, .model_config.ModelConfigError / PROVIDER_API_KEY_ENV /
# clear_caches, and .sessions.generate_thread_id are all unchanged. The
# RubricMiddleware bindings %%ask --review depends on survive too, including
# after_agent.__can_jump_to__ == ["model"] — the attribute that builds the
# conditional edge the revision loop travels.
#
# The one break was MCP. At 0.1.71 upstream swapped langchain-mcp-adapters for
# fastmcp and moved to mcp>=2; langchain-mcp-adapters 0.3.2 (the latest, with
# no newer release) pins mcp<2 and fails to import against mcp 2.x. There is no
# version pair giving 0.7.24 and the old adapter, so %%mcp now calls
# deepagents-code's own loader instead — see sage_magic.py's %%mcp magic.
RUN pip install --no-cache-dir \
    "jupyterlab==4.2.4" "notebook==7.2.2" \
    "deepagents-code==0.1.84" nest_asyncio folium geopandas matplotlib rasterio \
    ipywidgets ipyleaflet leafmap plotly pypdf openpyxl tomli-w tomlkit pyreadr

# Install PDAL via conda before pyforestscan — pip cannot build pdal from source without
# the system PDAL library, and pyforestscan pulls it in as a dependency.
RUN conda install -y -c conda-forge pdal python-pdal && conda clean -afy && \
    pip install --no-cache-dir pyforestscan laspy

# Google Drive client libraries used by ndp-workspaces / ndp-projects to fetch
# Drive resources when a shared review-account token is present in CephBlock.
# Bake into the image so the skill code doesn't have to pip install --user at
# runtime (which on this image triggers a split-install + namespace-package
# cache trap that requires a kernel restart to clear).
RUN pip install --no-cache-dir google-api-python-client google-auth-oauthlib google-api-core

# -----------------------------------------------------------------------------
# Step 2: Copy Assets
# -----------------------------------------------------------------------------
COPY sage_skills /tmp/build/skills
COPY apply_sage_patch.py /tmp/build/
COPY sage_magic.py /tmp/build/
COPY sage_kernel_backend.py /tmp/build/
COPY jupyter_server_config.py /tmp/build/
COPY jupyter_config.py /tmp/build/
COPY jupyterlab_overrides.json /tmp/build/
COPY review_rules.md /tmp/build/

# -----------------------------------------------------------------------------
# Step 3: Apply Sage Patches to config.py
# -----------------------------------------------------------------------------
RUN python /tmp/build/apply_sage_patch.py

# -----------------------------------------------------------------------------
# Step 4: Install Skills and Config for jovyan
# -----------------------------------------------------------------------------
RUN mkdir -p /home/jovyan/.deepagents/agent/skills && \
    cp -r /tmp/build/skills/* /home/jovyan/.deepagents/agent/skills/

# Write NRP provider config to jovyan's config.toml
#
# `max_retries` belongs under `[retries]`, NOT in the provider's `params`.
# deepagents-code 0.1.84 moved the retry budget from the provider SDK to a
# middleware: a `params.max_retries` is now stripped at model construction
# with a warning, and the model is built with `max_retries=0`. ARGUS installs
# that middleware itself (see sage_magic.py), and it reads the budget from
# this key.
#
# `mcp.tool_timeout` does NOT bound %%ask's MCP calls — it is set here only
# for the deepagents-code CLI, should anyone run it inside this image.
#
# The option is read by `mcp_tool_middleware`, which deepagents-code installs
# in its own agent factory. ARGUS builds its agent with `create_deep_agent`
# and attaches MCP tools itself, so that middleware is never in the graph;
# deleting this block entirely changes nothing for a notebook (verified — a
# 150 s call still completed, where the 120 s default would have cut it).
# 900 is MCP_TOOL_TIMEOUT_CEILING, the maximum deepagents accepts: the safest
# value to leave here in case ARGUS ever does route through that factory.
#
# What actually reaches the wire is fastmcp's httpx default,
# Timeout(connect=30, read=300), where `read` is a between-bytes timeout
# rather than a wall-clock budget. Long calls are therefore NOT capped —
# 330 s and 1000 s tool calls both returned clean on deepagents 0.7.24. The
# 5-minute cut v1.5.2 worked around was the same 300 acting as a hard total
# cap under langchain-mcp-adapters; fastmcp does not treat it that way.
RUN python -c "\
import tomli_w; \
from pathlib import Path; \
p = Path('/home/jovyan/.deepagents/config.toml'); \
tomli_w.dump({ \
    'models': { \
        'default': 'nrp:deepseek-v4-flash', \
        'providers': { \
            'nrp': { \
                'class_path': 'langchain_openai:ChatOpenAI', \
                'models': ['deepseek-v4-flash', 'glm-5'], \
                'api_key_env': 'NRP_API_KEY', \
                'base_url': 'https://ellm.nrp-nautilus.io/v1', \
                'params': {'temperature': 0, 'stream_chunk_timeout': 1200.0}, \
            } \
        } \
    }, \
    'mcp': {'tool_timeout': 900.0}, \
    'retries': {'max_retries': 6}, \
}, open(p, 'wb')); \
print('Written', p)"

# Deployment-level additions to the %%ask --review rubric, appended to the
# three base criteria by _load_review_rules() at kernel start. Criteria 4-6
# originated in ARGUS-FEDER and criterion 7 in the WEN-OKN app; all four are
# domain-general and are byte-identical across the three deployments, so a
# reviewed answer means the same thing in each. Must land before the chown
# below, which is what makes it readable by jovyan.
RUN cp /tmp/build/review_rules.md /home/jovyan/.deepagents/review_rules.md

# Install KernelShellBackend as an importable module in site-packages
RUN cp /tmp/build/sage_kernel_backend.py \
    "$(python -c 'import site; print(site.getsitepackages()[0])')/sage_kernel_backend.py"

# Register Sage magic commands for all Jupyter kernels
RUN mkdir -p /home/jovyan/.ipython/profile_default/startup && \
    cp /tmp/build/sage_magic.py \
       /home/jovyan/.ipython/profile_default/startup/00-sage-magic.py

# Store notebook trust DB and secret on persistent storage so signatures survive pod restarts.
# jupyter_config.py is loaded by ALL jupyter commands (including `jupyter trust` CLI).
# jupyter_server_config.py is loaded by Jupyter Server (JupyterLab).
# Both are needed so the same persistent paths are used by server and CLI.
RUN mkdir -p /home/jovyan/.jupyter && \
    cp /tmp/build/jupyter_server_config.py /home/jovyan/.jupyter/jupyter_server_config.py && \
    cp /tmp/build/jupyter_config.py /home/jovyan/.jupyter/jupyter_config.py

# Enable "Save Widget State Automatically" system-wide so maps and widgets survive
# notebook close/reopen (read-only — full interactivity still requires re-running the cell).
RUN LAB_SETTINGS_DIR="$(python -c 'import sys; print(sys.prefix)')/share/jupyter/lab/settings" && \
    mkdir -p "$LAB_SETTINGS_DIR" && \
    cp /tmp/build/jupyterlab_overrides.json "$LAB_SETTINGS_DIR/overrides.json"

# Source persistent .env in .bashrc so terminal users get NRP_API_KEY
# regardless of which task folder they work in.
RUN printf '\n# Sage: load NRP_API_KEY from persistent storage if available\n' \
        >> /home/jovyan/.bashrc && \
    echo 'DOTENV="/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env"' \
        >> /home/jovyan/.bashrc && \
    echo 'if [ -f "$DOTENV" ]; then set -a; source "$DOTENV"; set +a; fi' \
        >> /home/jovyan/.bashrc && \
    echo 'unset DOTENV' >> /home/jovyan/.bashrc

RUN mkdir -p /home/jovyan/.cache/pip && \
    chown -R jovyan:users /home/jovyan/.deepagents \
                          /home/jovyan/.ipython \
                          /home/jovyan/.jupyter \
                          /home/jovyan/.cache && \
    rm -rf /tmp/build

# -----------------------------------------------------------------------------
# Step 5: Switch to JupyterHub User
# -----------------------------------------------------------------------------
USER jovyan
WORKDIR /home/jovyan/work

# Point per-skill Learnings.md storage at the NRP CephBlock persistent mount.
# `/home/jovyan/work/_User-Persistent-Storage_CephBlock_/` survives pod
# restarts; `~/.sage_learnings/` (the portable code default) would be wiped
# every restart on NRP since `~/` is on the ephemeral pod filesystem.
# Override this env var at `docker run` time for non-NRP deployments.
ENV SAGE_LEARNINGS_PATH=/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.sage_learnings

EXPOSE 8888
CMD ["sleep", "infinity"]
