"""Apply Sage patches to the installed deepagents-code config.py.

Patches applied:
  1. detect_provider() — maps bare glm-* names to the 'nrp' provider.
  2. detect_provider() — maps the EXACT name 'deepseek-v4-flash' to
     'nrp'. This is NRP's hosted DeepSeek model and config.toml's
     default as of v1.5.3 (glm-5's NRP serving became quantized and
     effectively unusable — see feedback_use_glm5_in_docker_builds in
     project memory). The match must be exact, not a 'deepseek*'
     prefix: deepagents-code has its OWN native 'deepseek' provider
     branch (real deepseek.com API, via DEEPSEEK_API_KEY) that must
     keep working for other deepseek-* names a user might type.

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

_patched_any = False

# ── detect_provider: glm-* → nrp ─────────────────────────────────────────────
# Anchor on the nvidia branch ALONE and insert immediately after it, rather than
# on "nvidia branch followed by `return None`". Upstream appends new provider
# branches ahead of the final `return None` (0.1.54 inserted a Fireworks branch
# there, which silently broke the old anchor), so anchoring on the trailing
# `return None` rots on every such release. The nvidia branch itself is stable.
glm_anchor = (
    '    if model_lower.startswith(("nemotron", "nvidia/")):\n'
    '        return "nvidia"\n'
)
glm_insertion = (
    '\n'
    '    if model_lower.startswith("glm"):\n'
    '        return "nrp"\n'
)

if 'return "nrp"' in content:
    print("  ✓ detect_provider already maps glm -> nrp; nothing to do")
elif glm_anchor in content:
    content = content.replace(glm_anchor, glm_anchor + glm_insertion, 1)
    _patched_any = True
    print("  ✓ Patched detect_provider: glm -> nrp")
else:
    # Fail the build rather than ship an image whose bare `glm-*` model names do
    # not resolve to the NRP provider. Silently continuing here is how a broken
    # image reaches the pod unnoticed.
    print("  ✗ ERROR: glm detect_provider anchor not found — deepagents-code has")
    print("    changed its provider-detection code again.")
    print("    Fix: update `glm_anchor` in apply_sage_patch.py to match the new")
    print("    nvidia branch in deepagents_code/config.py, then rebuild.")
    sys.exit(1)

# ── detect_provider: exact 'deepseek-v4-flash' → nrp ─────────────────────────
# deepagents-code has its OWN native 'deepseek' branch (real deepseek.com API)
# that runs BEFORE the nvidia branch above, so extending the glm branch after
# nvidia is silently unreachable for any deepseek-* name — it never runs. This
# must anchor on, and run BEFORE, that native branch instead, and match the
# model name EXACTLY rather than by prefix: 'deepseek-v4-flash' is NRP's
# hosted model and config.toml's default as of v1.5.3, but other deepseek-*
# names (deepseek-chat, deepseek-reasoner, ...) must keep resolving to the
# real deepseek.com API for a user with DEEPSEEK_API_KEY set.
deepseek_anchor = (
    '    if model_lower.startswith("deepseek"):\n'
    '        return "deepseek"\n'
)
deepseek_insertion = (
    '    if model_lower == "deepseek-v4-flash":\n'
    '        return "nrp"\n'
    '\n'
)

if 'model_lower == "deepseek-v4-flash"' in content:
    print("  ✓ detect_provider already maps deepseek-v4-flash -> nrp; nothing to do")
elif deepseek_anchor in content:
    content = content.replace(deepseek_anchor, deepseek_insertion + deepseek_anchor, 1)
    _patched_any = True
    print("  ✓ Patched detect_provider: deepseek-v4-flash (exact) -> nrp")
else:
    print("  ✗ ERROR: deepseek detect_provider anchor not found — deepagents-code")
    print("    has changed its provider-detection code again.")
    print("    Fix: update `deepseek_anchor` in apply_sage_patch.py to match the")
    print("    new native deepseek branch in deepagents_code/config.py, then rebuild.")
    sys.exit(1)

if _patched_any:
    with open(config_path, "w") as f:
        f.write(content)

print("Done.")
