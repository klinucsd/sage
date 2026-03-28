---
name: skillsmp
description: Use for ALL skill marketplace operations on skillsmp.com. Triggers: "search marketplace", "search skill marketplace", "find skills for X", "browse marketplace", "install skill X", "show details of skill X", "list installed skills", "what skills are available". Always calls the SkillsMP API — never reads local skill files for marketplace searches.
---

# SkillsMP Skill Marketplace

SkillsMP (skillsmp.com) hosts over 600,000 AI agent skills. This skill lets you search the marketplace, view skill details, and install skills into Sage.

> **IMPORTANT:** When the user asks to search for skills or browse the marketplace, ALWAYS call the SkillsMP API. Do NOT read or search locally installed SKILL.md files. The marketplace has 600,000+ skills; local installs are only a handful.

## API Key

> **CRITICAL:** ALWAYS run the code below at the top of EVERY script — do NOT skip it, do NOT ask the user about the key, do NOT tell the user to set it up. Just run it. The key is already configured in the environment or in a .env file.

```python
import os
from pathlib import Path
from dotenv import load_dotenv

# Load from .env files (dotenv is already installed)
# Key sources (first found wins): environment variable → CWD .env → persistent storage .env
for env_path in [
    Path.cwd() / ".env",
    Path("/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env"),
]:
    if env_path.exists():
        load_dotenv(env_path, override=False)
        break

API_KEY = os.environ.get("SKILLSMP_API_KEY")
if not API_KEY:
    print("SKILLSMP_API_KEY not found. Set it via export, or add it to .env")
```

All API requests require the header: `Authorization: Bearer <API_KEY>`

**IMPORTANT: Always make API calls using Python scripts — never curl.** This keeps the key out of the displayed command and the notebook output. Never print or display the key value.

## Searching for Skills

### Keyword search (exact/partial match)
```
GET https://skillsmp.com/api/v1/skills/search?q=<keyword>
```
Use for specific tool names or technologies (e.g. "earthquake", "pandas", "web scraper").

### AI semantic search (natural language)
```
GET https://skillsmp.com/api/v1/skills/ai-search?q=<natural+language+query>
```
Use when the user describes what they want to do rather than naming a specific tool.
Returns results with a `score` field (0–1) indicating relevance.

### Response structure

Keyword search (`data.skills[]`):
```json
{
  "success": true,
  "data": {
    "skills": [
      {
        "id": "...",
        "name": "obspy",
        "author": "NeverSight",
        "description": "Seismology data processing...",
        "githubUrl": "https://github.com/NeverSight/.../obspy",
        "skillUrl": "https://skillsmp.com/skills/...",
        "stars": 93,
        "updatedAt": "1774280143"
      }
    ]
  }
}
```

AI search (`data.data[].skill` with `score`):
```json
{
  "success": true,
  "data": {
    "search_query": "expanded query...",
    "data": [
      {
        "score": 0.856,
        "skill": { ...same fields as above... }
      }
    ]
  }
}
```

### Displaying search results

Always display results as a markdown table with columns:
`Rank | Name | Author | Stars | Description`

For AI search, add a `Score` column showing the relevance score as a percentage (e.g. 0.856 → 86%).
Truncate description to ~100 characters if long.
Show at most 10 results.

## Looking Up a Specific Skill by Name

When the user asks for details about a named skill (e.g. "show me details of environmental-law"):

1. First check if it is already installed locally:
   ```python
   from pathlib import Path
   skill_dir = Path.home() / ".deepagents" / "agent" / "skills" / "<name>"
   if (skill_dir / "SKILL.md").exists():
       print((skill_dir / "SKILL.md").read_text())
   ```
2. If not installed, search the marketplace by exact name using keyword search:
   ```
   GET https://skillsmp.com/api/v1/skills/search?q=<name>
   ```
3. Find the matching skill in results, then fetch and display its SKILL.md using the raw GitHub URL (see "Viewing a Skill" section below).

## Viewing a Skill (SKILL.md content)

To show the full SKILL.md of a skill, fetch it directly from GitHub using the raw URL.

Convert `githubUrl` to raw URL:
- Replace `https://github.com/` → `https://raw.githubusercontent.com/`
- Replace `/tree/<branch>/` → `/<branch>/`
- Append `/SKILL.md`

Example:
```
githubUrl:  https://github.com/NeverSight/learn-skills.dev/tree/main/data/skills-md/steadfastasart/geoscience-skills/obspy
raw URL:    https://raw.githubusercontent.com/NeverSight/learn-skills.dev/main/data/skills-md/steadfastasart/geoscience-skills/obspy/SKILL.md
```

Fetch the raw URL with a GET request (no auth needed — it's public GitHub).
Display the SKILL.md content with syntax highlighting.

## Installing a Skill

Follow these steps **in order**. Do not use curl, wget, or any other method.

### Step 1 — Check if the skill is already installed

```python
from pathlib import Path
skill_name = "emergency-manager"  # replace with actual skill name
skill_dir = Path.home() / ".deepagents" / "agent" / "skills" / skill_name
if (skill_dir / "SKILL.md").exists():
    print(f"Skill '{skill_name}' is already installed at {skill_dir}")
```

If it already exists, stop here and tell the user it is already installed.

### Step 2 — Search the marketplace to get the GitHub URL

Use keyword search to find the skill by name (see "Searching for Skills" section).
Extract the `githubUrl` field from the matching result.

### Step 3 — Fetch the SKILL.md content from GitHub

Convert `githubUrl` to a raw GitHub URL (see "Viewing a Skill" section), then fetch it:

```python
import requests
import os
from pathlib import Path
from dotenv import load_dotenv

for env_path in [Path.cwd() / ".env", Path("/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env")]:
    if env_path.exists():
        load_dotenv(env_path, override=False)
        break

API_KEY = os.environ.get("SKILLSMP_API_KEY")
raw_url = "..."  # converted from githubUrl
headers = {"Authorization": f"Bearer {API_KEY}"}
resp = requests.get(raw_url, headers=headers, timeout=15)
skill_content = resp.text
print(skill_content[:200])  # preview to confirm it looks correct
```

### Step 4 — Write the skill to disk

```python
import re
name_match = re.search(r'^name:\s*(\S+)', skill_content, re.MULTILINE)
name = name_match.group(1) if name_match else "unknown-skill"
skill_dir = Path.home() / ".deepagents" / "agent" / "skills" / name
skill_dir.mkdir(parents=True, exist_ok=True)
(skill_dir / "SKILL.md").write_text(skill_content)
print(f"Installed skill '{name}' to {skill_dir}")
```

### Step 5 — Confirm installation

```python
installed_path = skill_dir / "SKILL.md"
size = installed_path.stat().st_size
print(f"Confirmed: {installed_path} ({size} bytes)")
```

After completing all steps, tell the user:

"✅ Skill **{name}** installed successfully.
- **Author**: {author}
- **Installed to**: {skill_dir}
- **File size**: {size} bytes

The skill is available in the next `%%ask` call (no kernel restart needed)."

## Listing Installed Skills

To list all currently installed skills, read all SKILL.md files under `~/.deepagents/agent/skills/`:

```python
from pathlib import Path
import re

skills_dir = Path.home() / ".deepagents" / "agent" / "skills"
installed = []
for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
    content = skill_md.read_text()
    name = re.search(r'^name:\s*(.+)', content, re.MULTILINE)
    desc = re.search(r'^description:\s*(.+)', content, re.MULTILINE)
    installed.append({
        "name": name.group(1).strip() if name else skill_md.parent.name,
        "description": desc.group(1).strip()[:100] if desc else ""
    })
```

Display as a markdown table: `Name | Description`

## Uninstalling a Skill

To uninstall a skill, remove its directory:
```python
import shutil
from pathlib import Path
skill_dir = Path.home() / ".deepagents" / "agent" / "skills" / "<name>"
if skill_dir.exists():
    shutil.rmtree(skill_dir)
    print(f"Uninstalled skill '{name}'")
```
