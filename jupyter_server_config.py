# Sage Jupyter server configuration
#
# Store the notebook trust notary database AND secret key on persistent
# storage so signatures survive container/pod restarts.
#
# The secret key is critical: if it changes (e.g. on pod restart), all
# existing signatures become invalid even if the DB is preserved.
#
#   - NRP JupyterHub: uses CephBlock persistent storage
#   - Local Docker:   uses the mounted work directory (-v ~/sage-workspace:/home/jovyan/work)
# Falls back to Jupyter defaults if neither path is available.

import os

_PERSISTENT = '/home/jovyan/work/_User-Persistent-Storage_CephBlock_'
_WORK = '/home/jovyan/work'

if os.path.isdir(_PERSISTENT):
    c.NotebookNotary.db_file = os.path.join(_PERSISTENT, '.jupyter_notary.db')
    c.NotebookNotary.secret_file = os.path.join(_PERSISTENT, '.jupyter_notary.key')
elif os.path.isdir(_WORK):
    c.NotebookNotary.db_file = os.path.join(_WORK, '.jupyter_notary.db')
    c.NotebookNotary.secret_file = os.path.join(_WORK, '.jupyter_notary.key')


# ---------------------------------------------------------------------------
# Pre-save hook: strip accumulated ipywidgets state from notebook metadata.
#
# Every `ipyl.GeoJSON(data=...)` call (or any large-data widget) embeds its
# data into `metadata.widgets.application/vnd.jupyter.widget-state+json`.
# JupyterLab serializes this on save, monotonically growing across reruns.
# A single USGS 3DEP coverage layer is ~8 MB; a few reruns push the notebook
# past 80 MB. `%reset` can't clear it because JupyterLab holds the
# notebook in memory and overwrites kernel-side file edits on next save.
#
# This hook runs server-side on EVERY save and is the proper place to fix it.
# We keep:
#   - cell sources and outputs (the user's actual content)
#   - the bare widget-view+json references (so live widgets re-render from a
#     cell's output if/when comm is re-established)
# We strip:
#   - metadata.widgets entirely
#
# Trade-off: on notebook reopen, widgets won't restore from saved state.
# The user must re-run cells to regain interactivity. This was already the
# case in practice for our use case — Sage's reopen-notice has been telling
# users to re-run cells for months.
# ---------------------------------------------------------------------------

def _strip_widget_state(model, **_):
    """nbformat pre_save_hook that removes metadata.widgets if present."""
    if model.get("type") != "notebook":
        return
    content = model.get("content")
    if not isinstance(content, dict):
        return
    metadata = content.get("metadata")
    if isinstance(metadata, dict) and "widgets" in metadata:
        del metadata["widgets"]


c.FileContentsManager.pre_save_hook = _strip_widget_state


# ---------------------------------------------------------------------------
# Post-save hook: re-sign the notebook so the trust signature stays current
# with on-disk content after our pre_save_hook modifications.
#
# Why this matters: the pre_save_hook strips metadata.widgets, which doesn't
# affect per-output trust hashes — but JupyterLab's in-memory trust state can
# fall out of sync between save → close-tab → reopen-tab, leaving inline
# <script> blocks in cell HTML outputs blocked from executing. The yellow
# "Re-run this cell" banner relies on JS to reveal itself, so without trust
# it stays hidden. Re-signing on every save keeps trust current; no manual
# `jupyter trust` invocation needed.
# ---------------------------------------------------------------------------

def _resign_notebook(model, os_path, contents_manager, **_):
    """Sign the freshly-saved notebook so JupyterLab treats it as trusted.

    Uses contents_manager.notary, which is already configured with the same
    db_file/secret_file we set above (so signatures persist across pod restart).
    Without this hook, every Ctrl+S after our pre_save_hook strips
    metadata.widgets leaves trust state stale — visible as missing yellow
    "re-run this cell" banners on reopen, because inline `<script>` blocks
    in cell HTML outputs only execute in trusted notebooks.
    """
    if model.get("type") != "notebook":
        return
    try:
        import nbformat
        nb = nbformat.read(os_path, as_version=4)
        contents_manager.notary.sign(nb)
    except Exception:
        # Never crash saves on a signing failure — worst case the user has
        # to re-run a cell to regain interactivity, which is the current
        # state without this hook.
        import traceback
        traceback.print_exc()


c.FileContentsManager.post_save_hook = _resign_notebook
