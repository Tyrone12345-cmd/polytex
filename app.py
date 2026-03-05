import os
import json
import platform
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session

# Native folder picker available on Windows/macOS with display
HAS_NATIVE_PICKER = False
try:
    if platform.system() in ('Windows', 'Darwin'):
        import tkinter as tk
        from tkinter import filedialog
        HAS_NATIVE_PICKER = True
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.urandom(24)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
ADMIN_PASSWORD = 'admin123'


def load_config():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    defaults = {
        'unbearbeitet_dir': os.path.join(base_dir, 'Unbearbeitet'),
        'bearbeitet_dir': os.path.join(base_dir, 'Bearbeitet')
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        # Ensure defaults are set
        for k, v in defaults.items():
            if not config.get(k):
                config[k] = v
        return config
    return defaults


def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


# ──────────────── User Routes ────────────────

@app.route('/')
def index():
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    unbearbeitet = []
    bearbeitet = []
    if ub_dir and os.path.isdir(ub_dir):
        unbearbeitet = sorted(f for f in os.listdir(ub_dir) if f.lower().endswith('.pdf'))
    if ba_dir and os.path.isdir(ba_dir):
        bearbeitet = sorted(f for f in os.listdir(ba_dir) if f.lower().endswith('.pdf'))
    configured = bool(ub_dir or ba_dir)
    return render_template('user.html', unbearbeitet=unbearbeitet, bearbeitet=bearbeitet, configured=configured)


@app.route('/pdf/<filename>')
def serve_pdf(filename):
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')

    safe_name = os.path.basename(filename)
    filepath = None

    # In beiden Ordnern direkt suchen
    for d in [ub_dir, ba_dir]:
        if d and os.path.isdir(d):
            candidate = os.path.join(d, safe_name)
            if os.path.exists(candidate):
                filepath = candidate
                break

    # Falls nicht gefunden: vielleicht wurde die Datei umbenannt — Info-JSONs durchsuchen
    if not filepath and ba_dir:
        sign_dir = os.path.join(ba_dir, '.unterschriften')
        if os.path.isdir(sign_dir):
            for f in os.listdir(sign_dir):
                if f.endswith('_info.json'):
                    info_path = os.path.join(sign_dir, f)
                    with open(info_path, 'r') as fh:
                        info = json.load(fh)
                    if info.get('original_filename') == safe_name:
                        new_name = info.get('new_filename', '')
                        candidate = os.path.join(ba_dir, new_name)
                        if os.path.exists(candidate):
                            filepath = candidate
                            break

    if not filepath:
        return 'Datei nicht gefunden', 404

    # Sicherheit: Pfad muss in einem der konfigurierten Ordner liegen
    real = os.path.realpath(filepath)
    allowed = False
    for d in [ub_dir, ba_dir]:
        if d and real.startswith(os.path.realpath(d)):
            allowed = True
            break
    if not allowed:
        return 'Zugriff verweigert', 403

    return send_file(filepath, mimetype='application/pdf')


@app.route('/sign/<filename>')
def sign_page(filename):
    safe_name = os.path.basename(filename)
    return render_template('sign.html', filename=safe_name)


@app.route('/api/extract-liefernummer/<filename>')
def extract_liefernummer(filename):
    import re
    safe_name = os.path.basename(filename)
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')

    if not ub_dir or not os.path.isdir(ub_dir):
        return jsonify({'liefernummer': ''})

    filepath = os.path.join(ub_dir, safe_name)
    real = os.path.realpath(filepath)
    if not real.startswith(os.path.realpath(ub_dir)) or not os.path.exists(filepath):
        return jsonify({'liefernummer': ''})

    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        text = ''
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + '\n'

        # Verschiedene Muster für Liefernummer / Lieferschein-Nr.
        patterns = [
            r'Lieferschein[\-\s]*(?:Nr\.?)?[:\s]*(\d[\d\s\-/]*\d)',
            r'Liefernummer[:\s]*(\d[\d\s\-/]*\d)',
            r'LS[\-\s]*Nr\.?[:\s]*(\d[\d\s\-/]*\d)',
            r'Liefer[\-\s]*Nr\.?[:\s]*(\d[\d\s\-/]*\d)',
            r'Lieferscheinnummer[:\s]*(\d[\d\s\-/]*\d)',
            r'Delivery[\s]*No\.?[:\s]*(\d[\d\s\-/]*\d)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                nummer = match.group(1).strip()
                return jsonify({'liefernummer': nummer})

        # Fallback: Nummer aus dem Dateinamen extrahieren
        name_match = re.search(r'(\d{4,})', os.path.splitext(safe_name)[0])
        if name_match:
            return jsonify({'liefernummer': name_match.group(1)})

    except Exception as e:
        print(f'Liefernummer-Extraktion fehlgeschlagen: {e}')

    return jsonify({'liefernummer': ''})


@app.route('/api/sign', methods=['POST'])
def save_signature():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Keine Daten erhalten'}), 400

    filename = os.path.basename(data.get('filename', ''))
    name = data.get('name', '').strip()
    liefernummer = data.get('liefernummer', '').strip()
    signature_data = data.get('signature', '')

    if not filename or not name or not liefernummer or not signature_data:
        return jsonify({'error': 'Name, Liefernummer und Unterschrift sind erforderlich'}), 400

    # Neuen Dateinamen erstellen: Name-Liefernummer-Bearbeitet.pdf
    safe_name = name.replace(' ', '')
    new_filename = f'{safe_name}-{liefernummer}-Bearbeitet.pdf'

    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    if not ub_dir or not ba_dir:
        return jsonify({'error': 'Verzeichnisse nicht konfiguriert'}), 500

    base_name = os.path.splitext(filename)[0]
    sign_dir = os.path.join(ba_dir, '.unterschriften')
    os.makedirs(sign_dir, exist_ok=True)

    # Info als JSON speichern
    import datetime
    info = {
        'lieferschein': filename,
        'original_filename': filename,
        'new_filename': new_filename,
        'name': name,
        'liefernummer': liefernummer,
        'datum': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    info_path = os.path.join(sign_dir, f'{base_name}_info.json')
    with open(info_path, 'w') as f:
        json.dump(info, f, indent=2, ensure_ascii=False)

    # Unterschrift als PNG speichern
    import base64
    if ',' in signature_data:
        signature_data = signature_data.split(',')[1]
    img_data = base64.b64decode(signature_data)
    img_path = os.path.join(sign_dir, f'{base_name}_unterschrift.png')
    with open(img_path, 'wb') as f:
        f.write(img_data)

    # Unterschrift + Name ins PDF einbetten
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.units import mm
        from io import BytesIO

        original_pdf_path = os.path.join(ub_dir, filename)
        reader = PdfReader(original_pdf_path)
        writer = PdfWriter()

        # Nur auf die letzte Seite die Unterschrift setzen
        last_page_index = len(reader.pages) - 1

        for i, page in enumerate(reader.pages):
            if i == last_page_index:
                # Overlay mit Unterschrift + Name erstellen
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)

                overlay_buffer = BytesIO()
                c = rl_canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))

                # Positionen: Name links, Unterschrift rechts, oberhalb des Footers
                y_base = 120  # Weit genug über dem Footer

                # Linie über alles
                c.setStrokeColorRGB(0.6, 0.6, 0.6)
                c.setLineWidth(0.5)
                c.line(40, y_base + 75, page_width - 40, y_base + 75)

                # Titel
                c.setFont('Helvetica-Bold', 10)
                c.drawString(40, y_base + 80, 'Empfangsbestätigung:')

                # Links: Name + Datum
                c.setFont('Helvetica', 10)
                c.drawString(40, y_base + 50, f'Name: {name}')
                c.drawString(40, y_base + 36, f'Datum: {info["datum"]}')

                # Rechts: Unterschrift-Bild
                sig_x = page_width - 220
                sig_y = y_base
                sig_width = 180
                sig_height = 70
                c.drawImage(img_path, sig_x, sig_y, width=sig_width, height=sig_height,
                           preserveAspectRatio=True, mask='auto')

                # Kleine Linie unter der Unterschrift
                c.setStrokeColorRGB(0, 0, 0)
                c.line(sig_x, sig_y - 2, sig_x + sig_width, sig_y - 2)
                c.setFont('Helvetica', 8)
                c.drawString(sig_x, sig_y - 12, 'Unterschrift')

                c.save()
                overlay_buffer.seek(0)

                overlay_reader = PdfReader(overlay_buffer)
                overlay_page = overlay_reader.pages[0]
                page.merge_page(overlay_page)

            writer.add_page(page)

        # Signiertes PDF speichern (Original bleibt, signierte Version extra)
        signed_pdf_path = os.path.join(sign_dir, f'{base_name}_signiert.pdf')
        with open(signed_pdf_path, 'wb') as f:
            writer.write(f)

        # Original-Backup erstellen (für späteres Wiederherstellen)
        import shutil
        backup_path = os.path.join(sign_dir, f'{base_name}_original.pdf')
        if not os.path.exists(backup_path):
            shutil.copy2(original_pdf_path, backup_path)

        # Signiertes PDF in den "Bearbeitet"-Ordner verschieben (mit neuem Namen)
        os.makedirs(ba_dir, exist_ok=True)
        dest = os.path.join(ba_dir, new_filename)
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(signed_pdf_path, dest)

        # Original-PDF aus Unbearbeitet entfernen
        if os.path.exists(original_pdf_path):
            os.remove(original_pdf_path)

    except Exception as e:
        # Wenn PDF-Einbettung fehlschlägt, trotzdem Erfolg melden (Unterschrift ist als PNG gespeichert)
        print(f'PDF-Einbettung fehlgeschlagen: {e}')

    return jsonify({'success': True, 'message': 'Unterschrift gespeichert', 'new_filename': new_filename})


@app.route('/api/status/<filename>')
def check_status(filename):
    safe_name = os.path.basename(filename)
    config = load_config()
    ba_dir = config.get('bearbeitet_dir', '')
    if not ba_dir:
        return jsonify({'signed': False})

    sign_dir = os.path.join(ba_dir, '.unterschriften')
    if not os.path.isdir(sign_dir):
        return jsonify({'signed': False})

    # Suche in allen Info-JSONs nach diesem Dateinamen (original oder neu)
    for f in os.listdir(sign_dir):
        if f.endswith('_info.json'):
            info_path = os.path.join(sign_dir, f)
            with open(info_path, 'r') as fh:
                info = json.load(fh)
            if info.get('new_filename') == safe_name or info.get('original_filename') == safe_name:
                return jsonify({'signed': True, 'info': info})

    return jsonify({'signed': False})


@app.route('/api/delete', methods=['POST'])
def delete_signature():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Keine Daten erhalten'}), 400

    filename = os.path.basename(data.get('filename', ''))
    if not filename:
        return jsonify({'error': 'Dateiname fehlt'}), 400

    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    if not ub_dir or not ba_dir:
        return jsonify({'error': 'Verzeichnisse nicht konfiguriert'}), 500

    sign_dir = os.path.join(ba_dir, '.unterschriften')

    # Info-JSON finden die zu dieser Datei gehört
    import shutil
    info = None
    info_base = None
    if os.path.isdir(sign_dir):
        for f in os.listdir(sign_dir):
            if f.endswith('_info.json'):
                fpath = os.path.join(sign_dir, f)
                with open(fpath, 'r') as fh:
                    candidate = json.load(fh)
                if candidate.get('new_filename') == filename or candidate.get('original_filename') == filename:
                    info = candidate
                    info_base = f.replace('_info.json', '')
                    break

    if not info:
        return jsonify({'error': 'Keine Unterschrift-Daten gefunden'}), 404

    original_filename = info.get('original_filename', filename)
    new_filename = info.get('new_filename', filename)

    # Backup (unsigniertes Original) zurück nach Unbearbeitet verschieben
    backup_path = os.path.join(sign_dir, f'{info_base}_original.pdf')
    if os.path.exists(backup_path):
        shutil.move(backup_path, os.path.join(ub_dir, original_filename))

    # Signierte Version aus Bearbeitet entfernen
    bearbeitet_path = os.path.join(ba_dir, new_filename)
    if os.path.exists(bearbeitet_path):
        os.remove(bearbeitet_path)

    # Unterschrift-Dateien löschen
    for suffix in ['_info.json', '_unterschrift.png', '_signiert.pdf']:
        path = os.path.join(sign_dir, f'{info_base}{suffix}')
        if os.path.exists(path):
            os.remove(path)

    return jsonify({'success': True, 'message': 'Unterschrift gelöscht'})


# ──────────────── Admin Routes ────────────────

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_panel'))
        return render_template('admin_login.html', error='Falsches Passwort')
    if session.get('is_admin'):
        return redirect(url_for('admin_panel'))
    return render_template('admin_login.html')


@app.route('/admin/panel')
def admin_panel():
    if not session.get('is_admin'):
        return redirect(url_for('admin'))
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    ub_count = len([f for f in os.listdir(ub_dir) if f.lower().endswith('.pdf')]) if ub_dir and os.path.isdir(ub_dir) else 0
    ba_count = len([f for f in os.listdir(ba_dir) if f.lower().endswith('.pdf')]) if ba_dir and os.path.isdir(ba_dir) else 0
    return render_template('admin.html', config=config, ub_count=ub_count, ba_count=ba_count)


@app.route('/admin/upload', methods=['POST'])
def admin_upload():
    if not session.get('is_admin'):
        return jsonify({'error': 'Nicht autorisiert'}), 403

    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    if not ub_dir:
        return jsonify({'error': 'Unbearbeitet-Ordner nicht konfiguriert'}), 400

    os.makedirs(ub_dir, exist_ok=True)

    files = request.files.getlist('pdfs')
    if not files:
        return jsonify({'error': 'Keine Dateien ausgewählt'}), 400

    uploaded = []
    for f in files:
        if f and f.filename and f.filename.lower().endswith('.pdf'):
            safe_name = os.path.basename(f.filename)
            dest = os.path.join(ub_dir, safe_name)
            f.save(dest)
            uploaded.append(safe_name)

    if not uploaded:
        return jsonify({'error': 'Keine gültigen PDF-Dateien'}), 400

    return jsonify({'success': True, 'count': len(uploaded), 'files': uploaded})


@app.route('/download/<filename>')
def download_pdf(filename):
    config = load_config()
    ba_dir = config.get('bearbeitet_dir', '')
    ub_dir = config.get('unbearbeitet_dir', '')

    safe_name = os.path.basename(filename)

    for d in [ba_dir, ub_dir]:
        if d and os.path.isdir(d):
            filepath = os.path.join(d, safe_name)
            real = os.path.realpath(filepath)
            if os.path.exists(filepath) and real.startswith(os.path.realpath(d)):
                return send_file(filepath, as_attachment=True, download_name=safe_name)

    return 'Datei nicht gefunden', 404


@app.route('/admin/save', methods=['POST'])
def admin_save():
    if not session.get('is_admin'):
        return jsonify({'error': 'Nicht autorisiert'}), 403

    data = request.get_json()
    key = data.get('key', '')
    directory = data.get('directory', '').strip()

    if key not in ('unbearbeitet_dir', 'bearbeitet_dir'):
        return jsonify({'error': 'Ungültiger Schlüssel'}), 400

    if not directory:
        return jsonify({'error': 'Verzeichnis darf nicht leer sein'}), 400

    if not os.path.isdir(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            return jsonify({'error': 'Verzeichnis konnte nicht erstellt werden'}), 400

    config = load_config()
    config[key] = directory
    save_config(config)

    return jsonify({'success': True, 'message': 'Gespeichert'})


@app.route('/admin/browse')
def admin_browse():
    if not session.get('is_admin'):
        return jsonify({'error': 'Nicht autorisiert'}), 403

    path = request.args.get('path', '/')
    path = os.path.realpath(path)

    if not os.path.isdir(path):
        return jsonify({'error': 'Ordner existiert nicht'}), 400

    folders = []
    try:
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full) and not entry.startswith('.'):
                folders.append(entry)
    except PermissionError:
        return jsonify({'error': 'Zugriff verweigert'}), 403

    parent = os.path.dirname(path) if path != '/' else None
    pdf_count = len([f for f in os.listdir(path) if f.lower().endswith('.pdf')]) if os.path.isdir(path) else 0

    return jsonify({'path': path, 'parent': parent, 'folders': folders, 'pdf_count': pdf_count})


@app.route('/admin/browse-native')
def admin_browse_native():
    if not session.get('is_admin'):
        return jsonify({'error': 'Nicht autorisiert'}), 403
    if not HAS_NATIVE_PICKER:
        return jsonify({'error': 'Nicht verfügbar'}), 501
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    folder = filedialog.askdirectory(title='Ordner auswählen')
    root.destroy()
    if folder:
        return jsonify({'success': True, 'path': folder})
    return jsonify({'success': False, 'path': ''})


@app.route('/admin/capabilities')
def admin_capabilities():
    if not session.get('is_admin'):
        return jsonify({'error': 'Nicht autorisiert'}), 403
    return jsonify({'native_picker': HAS_NATIVE_PICKER})


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))


if __name__ == '__main__':
    config = load_config()
    os.makedirs(config['unbearbeitet_dir'], exist_ok=True)
    os.makedirs(config['bearbeitet_dir'], exist_ok=True)
    save_config(config)
    app.run(debug=True, host='0.0.0.0', port=5000)
