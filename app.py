"""AdventureLand Control Center — Windows desktop entry point.
Run with: python app.py   (requires pywebview + WebView2 runtime)
"""
from __future__ import annotations
import sys
from pathlib import Path


def resource(name):
    root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    return root/name


def main():
    if sys.platform!='win32':
        print('Die Desktop-Oberfläche erfordert Windows 10/11. Offline-Tests sind plattformunabhängig.')
        return 2
    try:
        from alcc.qt_desktop import main as qt_main
    except ImportError as exc:
        print('Integrierte Qt-WebEngine fehlt: '+str(exc))
        print('Bitte Start-ControlCenter.ps1 verwenden, um PySide6 und PySide6-WebEngine zu installieren.')
        return 2
    return qt_main(resource(''))

if __name__=='__main__':raise SystemExit(main())
