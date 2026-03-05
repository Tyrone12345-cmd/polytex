# Polytex — Digitale Lieferschein-Unterschrift

Webbasierte Anwendung zur digitalen Signierung von PDF-Lieferscheinen. Mitarbeiter können Lieferscheine anzeigen, unterschreiben und verwalten — direkt im Browser.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-lightgrey?logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

- **PDF-Viewer** — Lieferscheine direkt im Browser anzeigen (via pdf.js)
- **Digitale Unterschrift** — Freihand-Signatur per Maus oder Touch auf Canvas
- **Automatische Liefernummer** — Wird per Regex aus dem PDF-Text extrahiert
- **PDF-Einbettung** — Unterschrift wird dauerhaft ins PDF eingebettet (reportlab)
- **Dateiverwaltung** — Automatische Umbenennung und Verschiebung nach Signierung
- **Backup & Undo** — Originale werden gesichert, Signaturen können rückgängig gemacht werden
- **Admin-Panel** — Ordnerpfade konfigurieren, Statistiken einsehen
- **Dark Mode** — Umschaltbares Light/Dark-Theme

## Screenshots

> *Screenshots hier einfügen*

## Voraussetzungen

- Python 3.10+
- pip

## Installation

```bash
# Repository klonen
git clone https://github.com/Tyrone12345-cmd/polytex.git
cd polytex

# Virtuelle Umgebung erstellen & aktivieren
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Abhängigkeiten installieren
pip install -r requirements.txt
```

## Konfiguration

Beim ersten Start über das Admin-Panel (`/admin`) die Ordnerpfade festlegen:

| Einstellung | Beschreibung |
|---|---|
| **Unbearbeitet-Ordner** | Ordner mit unsignierten PDF-Lieferscheinen |
| **Bearbeitet-Ordner** | Zielordner für signierte PDFs |

Die Konfiguration wird in `config.json` gespeichert.

## Starten

```bash
python app.py
```

Die Anwendung ist unter **http://localhost:5000** erreichbar.

## Nutzung

1. **Übersicht** — Startseite zeigt unbearbeitete und bearbeitete Lieferscheine in Tabs
2. **Unterschreiben** — PDF auswählen → Name und Liefernummer eingeben → Unterschrift zeichnen → Absenden
3. **Ergebnis** — PDF wird signiert, umbenannt (`Name-Liefernummer-Bearbeitet.pdf`) und in den Bearbeitet-Ordner verschoben
4. **Rückgängig** — Im Tab "Bearbeitet" kann die Signatur gelöscht und das Original wiederhergestellt werden

## Projektstruktur

```
polytex/
├── app.py                 # Flask-Backend (Routen, PDF-Verarbeitung)
├── config.json            # Ordnerkonfiguration (wird generiert)
├── requirements.txt       # Python-Abhängigkeiten
├── static/
│   ├── style.css          # Designsystem (Light/Dark-Theme)
│   └── signature.js       # Canvas-basierte Signaturerfassung
└── templates/
    ├── user.html           # Hauptübersicht (Tabs)
    ├── sign.html           # PDF-Viewer + Signaturformular
    ├── admin.html          # Admin-Dashboard
    └── admin_login.html    # Admin-Login
```

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Backend | Flask 3.1 |
| PDF-Verarbeitung | pypdf + reportlab |
| PDF-Anzeige | pdf.js 4.2 |
| Signatur | HTML5 Canvas |
| Styling | CSS Custom Properties |
| Speicher | Dateisystem (kein SQL) |

## Lizenz

Dieses Projekt steht unter der [MIT-Lizenz](LICENSE).
