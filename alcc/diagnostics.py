"""Only local checks; no implicit SSH, WSL, Ollama or Cline execution."""
from __future__ import annotations
from pathlib import Path
import shutil


def local_diagnostics(settings, coordinator):
    v=settings.values
    root=Path(v['coordinator_dir'])
    bot=Path(v['bot_repo_dir'])
    python=v['python_exe']
    node=v['node_exe']
    return {
        'mode':'read_only',
        'coordinator':{
            'path':str(root),
            'found':(root/'newbot_autonomy.py').is_file(),
            'directory_exists':root.is_dir(),
            'stop_markers':coordinator.stops(),
            'control_center_process_running':coordinator.is_running(),
        },
        'bot_repository':{'path':str(bot),'directory_exists':bot.is_dir(),
                          'has_git':(bot/'.git').exists()},
        'tools':{'python':python,'python_found':Path(python).is_file() or bool(shutil.which(python)),
                 'node':node,'node_found':Path(node).is_file() or bool(shutil.which(node)),
                 'ssh_found':bool(shutil.which('ssh')),'scp_found':bool(shutil.which('scp'))},
        'raspberry_pi':{'ssh_target':v['ssh_user']+'@'+v['ssh_host'],
                        'configured':True,'connectivity_checked':False,
                        'help':'Für eine echte Prüfung SSH & Dienst prüfen wählen. Beim ersten Zugriff Host-Fingerprint separat bestätigen.'},
        'note':'Keine Verbindung und kein Agent automatisch gestartet.'
    }
