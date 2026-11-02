# AdventureLand Control Center (Windows)

**Windows desktop app with two Qt WebEngine panes, bot diagnostics, one-click test/deploy and GitHub Release updates.**

## Windows EXE

Once a tagged stable Release is published, download **`AdventureLandControlCenter-Start.exe`** from [Releases](https://github.com/Riflex91/adventurelandcontrolcenter/releases). Keep that launcher in a convenient folder and start it whenever you want to use the Control Center. It checks GitHub Releases for a newer stable application version, verifies its SHA-256 checksum, installs it into a separate version directory, and launches the updated application automatically. No separate update/install/restart step is needed. With no internet connection, an already installed version starts instead. On the first start with no published release, the launcher cannot install the app.

The versioned application is stored in `%LOCALAPPDATA%\AdventureLandControlCenter\versions\vX.Y.Z`. **Existing settings, workspace candidates, audit history and the Qt chat profile stay in `%APPDATA%\AdventureLandControlCenter`.** The launcher does not touch the Raspberry Pi or coordinator state. A previous version is retained as fallback. Releases must be tagged and published by the repository owner.

## Integrated ChatGPT context

The original native **ChatGPT.com** WebEngine view opens inside the Control Center on launch. Local persisted project notes and the latest verified workspace are assembled into `%APPDATA%\AdventureLandControlCenter\chat-context-current.md`; the context is copied to the Windows clipboard. **Paste it once into ChatGPT (Ctrl+V)** to start the new conversation with the project facts. This does not silently upload local files or bypass browser isolation. Past ChatGPT conversations and unsaved notes cannot be extracted from the ChatGPT account by this application; only locally saved project context, workspace files and verified state are included. The Qt profile preserves login cookies where permitted by ChatGPT.

**True automatic context injection into ChatGPT.com is not supported by the public website integration.** A future opt-in first-party API integration could send context through the OpenAI API but would require separate API credentials and billing. No API keys are required or stored for this release.

## Build / security

A Windows GitHub Actions workflow runs unit tests, builds the native app and launcher using PyInstaller, and publishes assets only on an explicit `v*` git tag. The bundled app is not Authenticode-signed; Windows SmartScreen warnings are possible. Release package integrity is checked using the `.sha256` asset delivered from the same GitHub Release. For strong publisher authentication, add signing and a pinned key in a future release.

This project must not auto-clear coordinator stop markers, launch unsolicited deployments or weaken existing bot-deploy protections. Test code is not a complete security sandbox. Users remain responsible for selecting trusted branches/releases and for confirming production deployments.
