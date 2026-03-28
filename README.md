# Sage — Science Agent for Jupyter Notebooks

Sage is a natural language interface for Jupyter notebooks. You ask questions in plain English using the `%%ask` magic command, and Sage runs an AI agent that calls external data sources, writes and executes code, and delivers a structured report with inline maps, charts, and tables — all inside the notebook cell.

Sage is developed as part of the [National Data Platform](https://nationaldataplatform.org) (NDP) project, supported by the NSF. It is built on top of [deepagents-cli](https://pypi.org/project/deepagents-cli/).

---

## Features

- **Natural language queries** — ask data questions in plain English
- **Inline visualizations** — interactive Folium maps and PNG charts embedded directly in the agent's final report
- **Multi-layer maps** — GeoJSON and WMS layers combined on a single map
- **Cross-cell memory** — the agent remembers context from previous cells in the same notebook session
- **Extensible skills** — plug in new data sources by adding a skill folder
- **Multiple LLM providers** — works with NRP GLM, OpenAI, Anthropic, and any OpenAI-compatible endpoint

---

## Quick Start — Run Locally with Docker

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed
- An API key for a supported LLM provider (see [LLM Configuration](#llm-configuration))

### 1. Create a workspace folder

```bash
mkdir ~/sage-workspace
cd ~/sage-workspace
```

### 2. Add your API key

Create a `.env` file in the workspace folder:

```bash
# For NRP GLM (NRP JupyterHub users)
NRP_API_KEY=your_nrp_key_here

# For OpenAI
# OPENAI_API_KEY=sk-...

# For Anthropic
# ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Add your LLM configuration

Create a `config.toml` in the workspace folder. Example for OpenAI:

```toml
[models]
default = "openai:gpt-4o"

[models.providers.openai]
class_path = "langchain_openai:ChatOpenAI"
models = ["gpt-4o", "gpt-4-turbo"]
api_key_env = "OPENAI_API_KEY"
base_url = "https://api.openai.com/v1"

[models.providers.openai.params]
temperature = 0
```

Example for NRP GLM:

```toml
[models]
default = "nrp:glm-4.7"

[models.providers.nrp]
class_path = "langchain_openai:ChatOpenAI"
models = ["glm-4.7"]
api_key_env = "NRP_API_KEY"
base_url = "https://ellm.nrp-nautilus.io/v1"

[models.providers.nrp.params]
temperature = 0
```

### 4. Start Sage

```bash
docker run -p 8888:8888 \
  -v ~/sage-workspace:/home/jovyan/work \
  kaiucsd/sage-dev:v1.0.40 \
  jupyter lab --ip=0.0.0.0 --NotebookApp.token=''
```

### 5. Open JupyterLab

Open your browser at **http://localhost:8888**

Your notebooks and output files are saved to `~/sage-workspace/` and persist across container restarts.

> **Note:** Copy your `config.toml` into the container's config directory before running your first cell:
> ```python
> import shutil, os
> shutil.copy("/home/jovyan/work/config.toml", os.path.expanduser("~/.deepagents/config.toml"))
> ```
> Or run it once in a notebook cell. We plan to automate this in a future release.

---

## Using Sage in a Notebook

### Magic commands

```python
# Single-line prompt
%ask find earthquake events with magnitude > 5 near California in 2024

# Multi-line prompt (required when the prompt contains a '?')
%%ask
What GNSS stations are within 100 miles of the M5.7 Parker Butte earthquake
on December 9, 2024? Report the distance from the earthquake to each station.
```

> Use `%%ask` (cell magic) for any prompt containing `?`. The `?` character triggers IPython's help system when used in line magic.

### Output

After each `%%ask` run, Sage delivers:

- **Narration** — the agent thinks out loud before each tool call, explaining its intent
- **Tool call log** — collapsible details for every tool call (file reads, API requests, code execution)
- **Final report** — structured markdown with inline maps and images embedded where most relevant

Maps are rendered as interactive Folium maps with a layer control. Multiple GeoJSON and WMS layers that belong together are shown on a single map.

### Session state

Two variables are available in every notebook cell:

| Variable | Description |
|---|---|
| `SAGE_THREAD_ID` | 8-character session ID — reused across all cells for cross-cell memory |
| `SAGE_OUTPUT_DIR` | Path to the output folder for this notebook session |

Output files (GeoJSON, CSV, PNG, WMS configs) are saved to `SAGE_OUTPUT_DIR`, a folder named `_{notebook_name}_sage_/` created next to the notebook. This folder is cleared on kernel restart.

---

## Skills

Skills teach the agent how to access external data sources. Each skill is a folder containing a `SKILL.md` file that describes the data source and provides instructions for the agent.

### Built-in skills

| Skill | Data Source | Description |
|---|---|---|
| `usgs-earthquake-events` | USGS Earthquake Catalog API | Fetch seismic events as GeoDataFrames |
| `us-states` | KnowWhereGraph / FRINK SPARQL | US state geometries |
| `us-counties` | KnowWhereGraph / FRINK SPARQL | US county geometries |
| `ndp-search` | NDP OpenSearch catalog | Search the National Data Platform dataset catalog |
| `ndp-workspaces` | NDP Workspace API | List and filter NDP JupyterHub workspace configs |
| `kanawha-flood-depth` | USACE HEC-RAS / GeoServer WMS | Flood depth extent for the Kanawha River at a given flow (cfs) |
| `kanawha-reach-impact` | USACE / USGS gauges | Flood impacts per geographic reach along the Kanawha River |
| `kanawha-cikr-impact` | FEMA / USACE | Critical facilities (schools, hospitals, substations, etc.) impacted by Kanawha floods |
| `kanawha-nsi-impact` | FEMA NSI 2022 | Building-level flood impacts (structure counts, population, dollar damage) |

> The `usgs-earthquake-events`, `us-states`, and `us-counties` skills use fully public APIs and work without any special credentials. The `ndp-*` and `kanawha-*` skills require NDP or NRP access.

### Adding a custom skill

1. Create a folder under `sage_skills/your-skill-name/`
2. Add a `SKILL.md` with YAML frontmatter:

```markdown
---
name: your-skill-name
description: One sentence the agent uses to decide when to invoke this skill.
---

## Instructions

Tell the agent exactly how to use this data source: API endpoints,
parameters, response format, how to convert results to GeoDataFrames, etc.
```

3. Copy the skill into the container:
```bash
docker cp sage_skills/your-skill-name/ <container_id>:/home/jovyan/.deepagents/agent/skills/
```

Or rebuild the image with your skill included.

---

## LLM Configuration

Sage uses `~/.deepagents/config.toml` to configure the LLM provider. The format follows [deepagents-cli](https://pypi.org/project/deepagents-cli/) conventions.

Any OpenAI-compatible endpoint works. Examples:

| Provider | `class_path` | `base_url` | Key env var |
|---|---|---|---|
| NRP GLM | `langchain_openai:ChatOpenAI` | `https://ellm.nrp-nautilus.io/v1` | `NRP_API_KEY` |
| OpenAI | `langchain_openai:ChatOpenAI` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| Anthropic | `langchain_anthropic:ChatAnthropic` | _(not needed)_ | `ANTHROPIC_API_KEY` |

The API key is loaded from (first match wins):
1. `/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env` (NRP JupyterHub persistent storage)
2. `.env` in the current working directory (re-checked on every `%%ask` call)
3. Environment variable already set in the shell

---

## Docker Images

| Tag | Description |
|---|---|
| `kaiucsd/sage-dev:v1.0.40` | Latest development build |
| `kaiucsd/sage:v1.0` | _(planned)_ First formal release |

### Build from source

```bash
git clone https://github.com/klinucsd/sage.git
cd sage
docker build -t sage:jupyterhub .
```

### Dev iteration workflow

```bash
docker build -t sage:jupyterhub .
docker tag sage:jupyterhub kaiucsd/sage-dev:vX.Y.Z
docker push kaiucsd/sage-dev:vX.Y.Z
```

---

## NRP JupyterHub Deployment

On NRP JupyterHub, Sage runs as a pre-configured image. No local setup is needed.

- The `NRP_API_KEY` is loaded from `/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env`
- Output files are saved to `_{notebook_name}_sage_/` next to the notebook
- To trust a notebook after moving it: open a terminal and run `jupyter trust /path/to/notebook.ipynb`

---

## Demo Notebooks

| Notebook | Description |
|---|---|
| `earthquake_gnss.ipynb` | Earthquake event discovery → nearby GNSS station search → dataset download → signal analysis around earthquake time |
| `flood_impacts.ipynb` | Kanawha River flood depth visualization → schools, commercial buildings, and nursing homes at risk → population and damage estimates |

---

## Project

Sage is developed as part of the [National Data Platform](https://nationaldataplatform.org) (NDP) project, supported by the NSF. NDP provides a shared, open infrastructure for data sharing, discovery, and collaboration across scientific disciplines.

The name **Sage** stands for **Science Agent**.
