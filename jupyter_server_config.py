# Sage Jupyter server configuration
#
# Store the notebook trust (notary) database on persistent storage so that
# notebook signatures survive JupyterHub pod restarts.  Falls back to the
# default location (~/.local/share/jupyter/) when persistent storage is not
# available (e.g. local Docker run).

import os

_PERSISTENT = '/home/jovyan/work/_User-Persistent-Storage_CephBlock_'
if os.path.isdir(_PERSISTENT):
    c.NotebookNotary.db_file = os.path.join(_PERSISTENT, '.jupyter_notary.db')
