# =============================================================================
# Sage Docker Image - NRP JupyterHub Deployment
# =============================================================================
#
# Build:
#   docker build -t sage:jupyterhub .
#
# Tag and push (dev iterations):
#   docker tag sage:jupyterhub kaiucsd/sage-dev:v1.0.1
#   docker push kaiucsd/sage-dev:v1.0.1
#
# Formal release:
#   docker tag sage:jupyterhub kaiucsd/sage:v1.0
#   docker push kaiucsd/sage:v1.0
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
# [openai] extra provides langchain-openai (ChatOpenAI) for NRP's GLM endpoint
RUN pip install --no-cache-dir "deepagents-cli[openai]" nest_asyncio folium geopandas matplotlib rasterio \
    ipywidgets ipyleaflet leafmap plotly

# -----------------------------------------------------------------------------
# Step 2: Copy Assets
# -----------------------------------------------------------------------------
COPY sage_skills /tmp/build/skills
COPY apply_sage_patch.py /tmp/build/
COPY sage_magic.py /tmp/build/
COPY sage_kernel_backend.py /tmp/build/
COPY jupyter_server_config.py /tmp/build/
COPY jupyter_config.py /tmp/build/

# -----------------------------------------------------------------------------
# Step 3: Apply Sage Patches to config.py
# -----------------------------------------------------------------------------
RUN python /tmp/build/apply_sage_patch.py

# -----------------------------------------------------------------------------
# Step 4: Install Skills and Config for jovyan
# -----------------------------------------------------------------------------
RUN mkdir -p /home/jovyan/.deepagents/agent/skills && \
    cp -r /tmp/build/skills/* /home/jovyan/.deepagents/agent/skills/

# Copy any Python scripts bundled with skills to a shared runtime location.
# This is generic — Sage knows nothing about what these scripts do.
RUN mkdir -p /opt/sage_scripts && \
    find /tmp/build/skills -name "*.py" -exec cp {} /opt/sage_scripts/ \;

# Write NRP provider config to jovyan's config.toml
RUN python -c "\
import tomli_w; \
from pathlib import Path; \
p = Path('/home/jovyan/.deepagents/config.toml'); \
tomli_w.dump({ \
    'models': { \
        'default': 'nrp:glm-4.7', \
        'providers': { \
            'nrp': { \
                'class_path': 'langchain_openai:ChatOpenAI', \
                'models': ['glm-4.7'], \
                'api_key_env': 'NRP_API_KEY', \
                'base_url': 'https://ellm.nrp-nautilus.io/v1', \
                'params': {'temperature': 0}, \
            } \
        } \
    } \
}, open(p, 'wb')); \
print('Written', p)"

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

# Source persistent .env in .bashrc so terminal users get NRP_API_KEY
# regardless of which task folder they work in.
RUN printf '\n# Sage: load NRP_API_KEY from persistent storage if available\n' \
        >> /home/jovyan/.bashrc && \
    echo 'DOTENV="/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env"' \
        >> /home/jovyan/.bashrc && \
    echo 'if [ -f "$DOTENV" ]; then set -a; source "$DOTENV"; set +a; fi' \
        >> /home/jovyan/.bashrc && \
    echo 'unset DOTENV' >> /home/jovyan/.bashrc

RUN chown -R jovyan:users /home/jovyan/.deepagents \
                          /home/jovyan/.ipython \
                          /home/jovyan/.jupyter && \
    rm -rf /tmp/build

# -----------------------------------------------------------------------------
# Step 5: Switch to JupyterHub User
# -----------------------------------------------------------------------------
USER jovyan
WORKDIR /home/jovyan/work

EXPOSE 8888
CMD ["sleep", "infinity"]
