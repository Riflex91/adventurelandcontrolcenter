# AdventureLand Control Center 1.2.2 – Dauerbetrieb und Live-Konsole

Dieses Update ist für die **installierte Version 1.2.1-simple-workflows** bestimmt.
Es ändert ausschließlich Dateien der Windows-Control-Center-Anwendung.

## Änderungen

- **03 / Automatisch weiterarbeiten:** Startet `newbot_autonomy work --open-features --repairs -1 --session-minutes 0 --poll-seconds 30`. Es gibt kein festes Sitzungsende und kein `--once`. Es bleiben jedoch die internen 240-Minuten-Aufgabenbudgets, Sicherheitsstopps, das Verbot der automatischen Stop-Entfernung und andere Koordinatorfreigaben erhalten.
- **Manuelle Aufgabe:** Die 30-Minuten-Sitzung und die offiziellen Bedieneraufträge bleiben unverändert.
- **Live-Konsole:** Im Bereich 03 unmittelbar sichtbar; kombiniert Standardausgabe und Fehlerausgabe und aktualisiert sich alle 1,2 Sekunden. Auto-Scroll abschaltbar.
- **Protokolle:** Pro Sitzung unter `%APPDATA%\AdventureLandControlCenter\coordinator-console\run-<ID>.log`. Bis zu vier Teile je 8 MiB und Sitzung (ältere rotierte Teile werden verworfen). Die GUI hält zusätzlich nur einen begrenzten Textpuffer.
- **Stoppen:** Weiterhin der vorhandene Stopp-Knopf des Koordinators. Stop-Marker werden von diesem Update nicht entfernt.

**Wichtige Betriebsgrenze:** „Dauerbetrieb“ bedeutet kein Sitzungszeitlimit, nicht garantierte ununterbrochene Arbeit. Der Koordinator kann bei BLOCKED, Sicherheitsfehlern oder fehlender Readiness weiterhin pausieren/abbrechen. Die UI erzwingt weder eine Freigabe noch ein Deployment.

## Windows Installation

Control Center vollständig schließen. ZIP nach `%USERPROFILE%\Downloads\ALCC-Continuous-1.2.2` entpacken.

Preflight:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\ALCC-Continuous-1.2.2\Install-ContinuousUI.ps1"
```

Danach (nur bei `CONTINUOUS_UI_PREFLIGHT=PASS`):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\ALCC-Continuous-1.2.2\Install-ContinuousUI.ps1" -Apply
```

Start:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\AdventureLand-Control-Center\Start-ControlCenter.ps1" -NoInstall
```

Der Installer prüft vor Änderungen das komplette bisherige Release-Inventar sowie alle Patch-Hashes, erstellt ein Backup, führt die lokalen Tests aus und macht bei Fehlern einen Rollback. Er verändert keinen Pi oder Koordinator und startet keine Agenten. Die tatsächliche Windows-Ausführung ist erst nach dem Test auf deinem PC bestätigt.
