"""Apply Sage patches to the installed deepagents-code config.py.

Patches applied:
  1. detect_provider() — maps glm-* model names to the 'nrp' provider

NOTE: the interactive-agent package was renamed from `deepagents-cli`
(0.0.x) to `deepagents-code` (0.1.x) starting June 2026. This patch
script targets the new package.
"""

import os
import sys
import sysconfig

site = sysconfig.get_path("purelib")
config_path = os.path.join(site, "deepagents_code", "config.py")

print(f"Patching {config_path}")
with open(config_path) as f:
    content = f.read()

# ── detect_provider: glm-* → nrp ─────────────────────────────────────────────
# Anchor on the nvidia branch ALONE and insert immediately after it, rather than
# on "nvidia branch followed by `return None`". Upstream appends new provider
# branches ahead of the final `return None` (0.1.54 inserted a Fireworks branch
# there, which silently broke the old anchor), so anchoring on the trailing
# `return None` rots on every such release. The nvidia branch itself is stable.
anchor = (
    '    if model_lower.startswith(("nemotron", "nvidia/")):\n'
    '        return "nvidia"\n'
)
insertion = (
    '\n'
    '    if model_lower.startswith("glm"):\n'
    '        return "nrp"\n'
)

if 'return "nrp"' in content:
    # Already patched (e.g. a rebuilt layer over a patched tree) — do nothing.
    print("  ✓ detect_provider already maps glm -> nrp; nothing to do")
elif anchor in content:
    content = content.replace(anchor, anchor + insertion, 1)
    with open(config_path, "w") as f:
        f.write(content)
    print("  ✓ Patched detect_provider: glm -> nrp")
else:
    # Fail the build rather than ship an image whose bare `glm-*` model names do
    # not resolve to the NRP provider. Silently continuing here is how a broken
    # image reaches the pod unnoticed.
    print("  ✗ ERROR: detect_provider anchor not found — deepagents-code has")
    print("    changed its provider-detection code again.")
    print("    Fix: update `anchor` in apply_sage_patch.py to match the new")
    print("    nvidia branch in deepagents_code/config.py, then rebuild.")
    sys.exit(1)

print("Done.")
