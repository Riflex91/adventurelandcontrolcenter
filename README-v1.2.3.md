# AdventureLand Control Center 1.2.3 — unbegrenzte manuelle Aufträge und Ollama-Aufräumen

Dieses Update unterstützt Control Center **1.2.1-simple-workflows** oder **1.2.2-continuous-console** und enthält einen eng begrenzten, separat prüfbaren Kompatibilitätspatch für den installierten Koordinator.

## Änderungen

- „Automatisch weiterarbeiten“: weiterhin ohne Sitzungszeitlimit. Erkennt aktive Sitzungssperren (`safety_hold`, `needs_attention`) oder gesetzte `stop.request`/`session.stop`, fordert (wenn erforderlich) den regulären `newbot_autonomy stop` an und wartet auf das reguläre Ende. Die Stop-Marker werden **niemals gelöscht**.
- „Manuelle Aufgabe erteilen“: ebenfalls ohne Sitzungszeitlimit. Der Koordinator schreibt die Aufgabe nach wie vor in seinen vorhandenen Bedienerauftragsspeicher, prüft den Abschluss unabhängig und bewahrt die zeitlichen **Einzelaufgabenbudgets** und sämtliche Sicherheits- und Deployment-Gates. Auch ein unbegrenzter Auftrag kann bei Fehlern oder einem Stop gestoppt werden, ohne als erledigt ausgegeben zu werden.
- Ollama: Nach dem Ende der Control-Center-Koordinatorsitzung wird das feste Koordinator-Modell `qwen3.8:27b` mit `keep_alive=0` über die lokale Ollama-API entladen. War der Ollama-Server zuvor nicht aktiv und wurde von dieser Anwendung gestartet, wird dieser ebenfalls beendet. Ein **bereits unabhängig laufender Ollama-Server wird nicht zwangsweise beendet**, da andere Programme ihn benutzen könnten. Die Live-Konsole zeigt dieses Ergebnis an.
- Kompatibilitätskorrektur im **Koordinator-Quellcode**: `autonomy_interactive.py` und `autonomy_session.py` werden ausschließlich bei exakter SHA-256-Baseline gepatcht; es werden keine Stop-Dateien oder Entwicklungszustände verändert. Ohne diese geprüfte Korrektur verweigert die GUI einen vermeintlich unbegrenzten manuellen Start, statt heimlich nach vier Stunden aufzuhören.
- Live-Konsole und alle bestehenden Chat-, Auto-Sync-, Test- und Deploymentfunktionen bleiben erhalten.

## Installation (Windows 11)

Control Center und Koordinator/Cline-Prozesse vorher vollständig schließen. ZIP in `%USERPROFILE%\Downloads\ALCC-Unlimited-SafeStop-1.2.3` entpacken.

Vorprüfung ohne Änderungen:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\ALCC-Unlimited-SafeStop-1.2.3\Install-SafeStop.ps1"
```

Danach nur bei `SAFE_STOP_PREFLIGHT=PASS`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\ALCC-Unlimited-SafeStop-1.2.3\Install-SafeStop.ps1" -Apply
```

Programm starten:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\AdventureLand-Control-Center\Start-ControlCenter.ps1" -NoInstall
```

Der Installer erstellt getrennte Backups der modifizierten Anwendungs- und Koordinatordateien, prüft die Inventare und führt nur lokale Tests aus. Das Pi-System wird nicht angesprochen. Er schützt und vergleicht `stop.request`, `session.stop`, `state.json`, `session.json`, `development/state.json`, `development/work-progress.json` und `readiness.json` vor/nach der Installation.

**Hinweis:** Der zugrunde liegende Koordinator hat weiterhin eigene Aufgabenbudgets, Readiness- und Stop-Regeln. "Ohne Zeitlimit" garantiert keinen erfolgreichen Abschluss; eine Sperre muss gesondert behandelt werden. Es werden weder stop.request noch session.stop entfernt.

### Bestätigter Koordinatorstand

Dieses Kompatibilitätsupdate bewahrt zusätzliche Bedienerstop-, Runtime-Preflight- und Statusfunktionen aus den am 2. November bereitgestellten Koordinatorquellen. Die GUI prüft vor jedem manuellen Start die exakten SHA-256-Werte der entsprechend angepassten Dateien.
