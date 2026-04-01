# Sage Jupyter server configuration
#
# Store the notebook trust (notary) database on a persistent path so that
# notebook signatures survive container/pod restarts:
#   - NRP JupyterHub: uses CephBlock persistent storage
#   - Local Docker:   uses the mounted work directory (-v ~/sage-workspace:/home/jovyan/work)
# Falls back to the Jupyter default (~/.local/share/jupyter/) if neither is available.

import os

_PERSISTENT = '/home/jovyan/work/_User-Persistent-Storage_CephBlock_'
_WORK = '/home/jovyan/work'

if os.path.isdir(_PERSISTENT):
    # NRP JupyterHub: persistent CephBlock storage survives pod restarts
    c.NotebookNotary.db_file = os.path.join(_PERSISTENT, '.jupyter_notary.db')
elif os.path.isdir(_WORK):
    # Local Docker: mounted workspace volume survives container restarts
    c.NotebookNotary.db_file = os.path.join(_WORK, '.jupyter_notary.db')
