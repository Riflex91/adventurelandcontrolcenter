# AdventureLand Control Center 1.2.1 — Vereinfachte Bedienung

## Was sich geändert hat

**02 ChatGPT / Bot:** Die Arbeitsbereich-Auswahl für „Starte neuen Chat“ entfällt. Vor dem Chatwechsel prüft die Oberfläche den aktiven Pi-Release und synchronisiert bei Bedarf einen neuen lokalen Snapshot. Der eigentliche Chatwechsel akzeptiert nur einen frischen, gegen die lokalen Snapshots validierten AutoSync-Beleg (unter 75 Sekunden alt). Fehlt dieser, bleibt der Chatwechsel gesperrt. Der alte Chat wird erst nach bestätigter Übergabe geschlossen.

**03 Koordinator / KI:** Zwei deutlich sichtbare Arbeitsweisen: „Automatisch weiterarbeiten“ oder „Manuelle Aufgabe erteilen“. Bei manueller Arbeit gibst du einen Auftrag von 1–4000 Zeichen ein und startest nach ausdrücklicher Bestätigung die bereits vorhandene *interactive*-Bedienerauftragsfunktion von `newbot_autonomy.py`. Der Auftrag wird als Konsoleneingabe übergeben, **niemals als ausführbarer Befehl oder Python-Code**. Beide Arbeitsarten nutzen eine begrenzte Sitzung von maximal 30 Minuten. Die Ausgabe kann bei Bedarf aufgeklappt werden. Die Stop-Taste bleibt verfügbar.

**Sicherheit:** Beide Koordinator-Startarten prüfen `stop.request` und `session.stop` und entfernen sie nicht. Die bestehenden Projekt-, Reparatur-, Entwicklungs-, Deployment- und Approval-Gates werden weder zurückgesetzt noch umgangen. Ein Start darf nur erfolgen, wenn der Koordinator aktuell tatsächlich freigegeben ist. Ein Pi-Snapshot ist nur lesend; kein automatisches Deployment.

## Installation

Entpacke die ZIP-Datei nach `%USERPROFILE%\Downloads\ALCC-Simple-UI-1.2.1` und schließe das Control Center. Dann in Windows PowerShell 5.1:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\ALCC-Simple-UI-1.2.1\Install-SimpleUI.ps1"
```

Nach `SIMPLE_UI_PREFLIGHT=PASS`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\ALCC-Simple-UI-1.2.1\Install-SimpleUI.ps1" -Apply
```

Start danach:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\AdventureLand-Control-Center\Start-ControlCenter.ps1" -NoInstall
```

Die Installation ersetzt **nur** `alcc/api.py`, `alcc/coordinator.py`, `alcc/qt_desktop.py` und `assets/index.html`, ergänzt einen Test sowie diese Dokumentation und schreibt das signalisierte Dateiinventar. Es verändert keine Koordinator-Dateien, keine Bot-Kandidaten, keine Stop-Marker, keinen Raspberry Pi und keine vorhandene README. Vor Anwendung werden sämtliche Manifestdateien geprüft; die Installation hat Backup und Rollback. Beim Installieren laufen Offline-Tests mit Mock-/Fixture-Daten, keine Koordinator-Sitzung und keine SSH-Verbindung.

Die neue GUI/Koordinator-Kombination wurde **offline** geprüft. Eine tatsächliche Windows-Ausführung einschließlich Ollama/Cline wurde noch nicht getestet. Sie arbeitet absichtlich nicht um Stop- oder Freigabesperren herum. Falls eine Sperre aktiv ist, wird sie beim Klick angezeigt; die Entwicklung bleibt gestoppt.
