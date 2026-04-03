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
