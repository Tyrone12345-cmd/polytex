import os
import re
import json
import shutil
import base64
import secrets
import logging
import datetime
import platform
from io import BytesIO
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    send_file, redirect, url_for, session, abort
)
from flask_socketio import SocketIO
from watcher import FileWatcher

# ──────────────── Configuration ────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')


def _load_env():
    """Load .env file if present."""
    env_path = os.path.join(BASE_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    os.environ.setdefault(key.strip(), value.strip())


_load_env()

# Persistent secret key — survives restarts
_secret_key_file = os.path.join(BASE_DIR, '.secret_key')
if os.environ.get('SECRET_KEY'):
    _secret_key = os.environ['SECRET_KEY']
elif os.path.exists(_secret_key_file):
    with open(_secret_key_file, 'r') as f:
        _secret_key = f.read().strip()
else:
    _secret_key = secrets.token_hex(32)
    with open(_secret_key_file, 'w') as f:
        f.write(_secret_key)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('polytex')


def load_config():
    defaults = {
        'unbearbeitet_dir': os.path.join(BASE_DIR, 'Unbearbeitet'),
        'bearbeitet_dir': os.path.join(BASE_DIR, 'Bearbeitet')
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        for k, v in defaults.items():
            if not config.get(k):
                config[k] = v
        return config
    return defaults


def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


# ──────────────── App & Extensions ────────────────

app = Flask(__name__)
app.secret_key = _secret_key
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

socketio = SocketIO(app, async_mode='threading', cors_allowed_origins=[])

# Native folder picker (Windows/macOS desktop only)
HAS_NATIVE_PICKER = False
try:
    if platform.system() in ('Windows', 'Darwin'):
        import tkinter as tk
        from tkinter import filedialog
        HAS_NATIVE_PICKER = True
except ImportError:
    pass


# ──────────────── File Watcher (Real-time Updates) ────────────────

_watcher = FileWatcher()


def _on_files_changed():
    """Push updated file lists to all connected clients via WebSocket."""
    config = load_config()
    socketio.emit('files_updated', {
        'unbearbeitet': _get_pdfs(config.get('unbearbeitet_dir', '')),
        'bearbeitet': _get_pdfs(config.get('bearbeitet_dir', ''))
    })


_watcher.set_callback(_on_files_changed)


def _start_watcher():
    config = load_config()
    dirs = [config.get(k, '') for k in ('unbearbeitet_dir', 'bearbeitet_dir')]
    _watcher.watch(dirs)


# ──────────────── Helpers ────────────────

def _get_pdfs(directory):
    """Return sorted list of PDF filenames in a directory."""
    if directory and os.path.isdir(directory):
        return sorted(f for f in os.listdir(directory) if f.lower().endswith('.pdf'))
    return []


def _safe_name(filename):
    """Sanitize filename to prevent path traversal."""
    return os.path.basename(filename)


def _is_within(filepath, directory):
    """Verify filepath resolves inside directory."""
    real_file = os.path.realpath(filepath)
    real_dir = os.path.realpath(directory)
    return real_file.startswith(real_dir + os.sep) or real_file == real_dir


def _generate_csrf():
    """Generate CSRF token stored in session."""
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_hex(32)
    return session['_csrf']


def _check_csrf():
    """Validate CSRF token from header or form field."""
    token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
    expected = session.get('_csrf', '')
    if not token or not expected or not secrets.compare_digest(token, expected):
        abort(403)


def admin_required(f):
    """Decorator: no-op (password protection removed)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


# ──────────────── Security Middleware ────────────────

@app.before_request
def csrf_protect():
    """Enforce CSRF token on all state-changing requests."""
    if request.method in ('POST', 'PUT', 'DELETE'):
        if request.path.startswith('/socket.io'):
            return
        _check_csrf()


@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' ws: wss: https://cdnjs.cloudflare.com;"
    )
    return response


@app.context_processor
def inject_csrf():
    return {'csrf_token': _generate_csrf()}


# ──────────────── User Routes ────────────────

@app.route('/')
def index():
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    return render_template(
        'user.html',
        unbearbeitet=_get_pdfs(ub_dir),
        bearbeitet=_get_pdfs(ba_dir),
        configured=bool(ub_dir or ba_dir)
    )


@app.route('/pdf/<filename>')
def serve_pdf(filename):
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    safe = _safe_name(filename)
    filepath = None

    # 1) Check Unbearbeitet (flat)
    if ub_dir and os.path.isdir(ub_dir):
        candidate = os.path.join(ub_dir, safe)
        if os.path.isfile(candidate) and _is_within(candidate, ub_dir):
            filepath = candidate

    # 2) Check Bearbeitet subfolders
    if not filepath and ba_dir and os.path.isdir(ba_dir):
        for entry in os.listdir(ba_dir):
            sub = os.path.join(ba_dir, entry)
            if not os.path.isdir(sub):
                continue
            # Serve by folder name
            if entry == safe:
                candidate = os.path.join(sub, 'Bearbeitet.pdf')
                if os.path.isfile(candidate):
                    filepath = candidate
                    break
            # Serve by original filename match
            info_path = os.path.join(sub, 'info.json')
            if os.path.isfile(info_path):
                with open(info_path, 'r') as fh:
                    info = json.load(fh)
                if info.get('original_filename') == safe:
                    candidate = os.path.join(sub, 'Bearbeitet.pdf')
                    if os.path.isfile(candidate):
                        filepath = candidate
                        break

    if not filepath:
        abort(404)

    real = os.path.realpath(filepath)
    for d in [ub_dir, ba_dir]:
        if d and real.startswith(os.path.realpath(d)):
            return send_file(filepath, mimetype='application/pdf')
    abort(403)


@app.route('/sign/<filename>')
def sign_page(filename):
    return render_template('sign.html', filename=_safe_name(filename))


@app.route('/api/files')
def api_files():
    """Return current file lists as JSON (polling fallback)."""
    config = load_config()
    return jsonify({
        'unbearbeitet': _get_pdfs(config.get('unbearbeitet_dir', '')),
        'bearbeitet': _get_pdfs(config.get('bearbeitet_dir', ''))
    })


def _clean_liefernummer(raw):
    """Normalize extracted number: remove internal whitespace, trim separators."""
    cleaned = re.sub(r'\s+', '', raw)
    return cleaned.strip(' -/.)')


def _normalize_pdf_text(text):
    """Normalize PDF text for reliable pattern matching."""
    # Replace Unicode dashes with ASCII hyphen
    text = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]', '-', text)
    # Collapse multiple spaces/tabs into one
    text = re.sub(r'[ \t]+', ' ', text)
    return text


# Lieferschein patterns ordered by confidence (most specific first)
_LIEF_PATTERNS_HIGH = [
    r'Lieferschein[\-\s]*(?:Nr\.?|Nummer|No\.?)?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Lieferschein[\-\s]*(?:Nr\.?|Nummer|No\.?)?[:\s#]*(\d{3,})',
    r'Liefernummer[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Liefernummer[:\s#]*(\d{3,})',
    r'Lieferscheinnummer[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Lieferscheinnummer[:\s#]*(\d{3,})',
    r'Liefer[\-\s]*(?:schein[\-\s]*)?Nr\.?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Liefer[\-\s]*(?:schein[\-\s]*)?Nr\.?[:\s#]*(\d{3,})',
    r'LS[\-\s]*Nr\.?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'LS[\-\s]*Nr\.?[:\s#]*(\d{3,})',
    r'Lfs\.?[\-\s]*Nr\.?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Lfs\.?[\-\s]*Nr\.?[:\s#]*(\d{3,})',
]

_LIEF_PATTERNS_MEDIUM = [
    r'Delivery[\s]*(?:Note)?[\s]*(?:No\.?|Number)?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Delivery[\s]*(?:Note)?[\s]*(?:No\.?|Number)?[:\s#]*(\d{3,})',
    r'Beleg[\-\s]*Nr\.?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Belegnummer[:\s#]*(\d[\d\s\-/.]*\d)',
    r'WA[\-\s]*Nr\.?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Warenausgangs?[\-\s]*Nr\.?[:\s#]*(\d[\d\s\-/.]*\d)',
    r'Versand[\-\s]*Nr\.?[:\s#]*(\d[\d\s\-/.]*\d)',
]


@app.route('/api/extract-liefernummer/<filename>')
def extract_liefernummer(filename):
    safe = _safe_name(filename)
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')

    if not ub_dir or not os.path.isdir(ub_dir):
        return jsonify({'liefernummer': ''})

    filepath = os.path.join(ub_dir, safe)
    if not os.path.isfile(filepath) or not _is_within(filepath, ub_dir):
        return jsonify({'liefernummer': ''})

    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)

        # Extract text per page for prioritized search
        pages_text = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pages_text.append(_normalize_pdf_text(page_text))

        # 1) High-confidence patterns — search first page first, then remaining
        for scope in (pages_text[:1], pages_text[1:]):
            search_text = '\n'.join(scope)
            if not search_text:
                continue
            for pattern in _LIEF_PATTERNS_HIGH:
                match = re.search(pattern, search_text, re.IGNORECASE)
                if match:
                    return jsonify({'liefernummer': _clean_liefernummer(match.group(1))})

        # 2) Medium-confidence patterns — full text
        full_text = '\n'.join(pages_text)
        for pattern in _LIEF_PATTERNS_MEDIUM:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                return jsonify({'liefernummer': _clean_liefernummer(match.group(1))})

        # 3) Filename-based fallback
        basename = os.path.splitext(safe)[0]

        # Specific filename patterns (LS-123456, Lieferschein_789012)
        fn_patterns = [
            r'(?:LS|LF|Lief(?:erschein)?)[\-_\s]*(\d{4,})',
        ]
        for pattern in fn_patterns:
            fn_match = re.search(pattern, basename, re.IGNORECASE)
            if fn_match:
                return jsonify({'liefernummer': fn_match.group(1)})

        # Generic long number from filename (last resort)
        name_match = re.search(r'(\d{4,})', basename)
        if name_match:
            return jsonify({'liefernummer': name_match.group(1)})

    except Exception as e:
        logger.warning('Liefernummer extraction failed: %s', e)

    return jsonify({'liefernummer': ''})


@app.route('/api/sign', methods=['POST'])
def save_signature():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Keine Daten erhalten'}), 400

    filename = _safe_name(data.get('filename', ''))
    name = data.get('name', '').strip()
    liefernummer = data.get('liefernummer', '').strip()
    signature_data = data.get('signature', '')

    if not filename or not name or not liefernummer or not signature_data:
        return jsonify({'error': 'Name, Liefernummer und Unterschrift sind erforderlich'}), 400

    # Sanitize for folder/file names
    safe_name_part = re.sub(r'[^\w\-]', '', name.replace(' ', ''))
    safe_lief = re.sub(r'[^\w\-]', '', liefernummer)

    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    if not ub_dir or not ba_dir:
        return jsonify({'error': 'Verzeichnisse nicht konfiguriert'}), 500

    original_pdf = os.path.join(ub_dir, filename)
    if not os.path.isfile(original_pdf) or not _is_within(original_pdf, ub_dir):
        return jsonify({'error': 'Originaldatei nicht gefunden'}), 404

    # Create unique subfolder: Liefernummer_Name (with counter for duplicates)
    folder_base = f'{safe_lief}_{safe_name_part}'
    folder_path = os.path.join(ba_dir, folder_base)
    if os.path.exists(folder_path):
        counter = 2
        while os.path.exists(os.path.join(ba_dir, f'{folder_base}_{counter}')):
            counter += 1
        folder_base = f'{folder_base}_{counter}'
        folder_path = os.path.join(ba_dir, folder_base)
    os.makedirs(folder_path, exist_ok=True)

    datum = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    info = {
        'lieferschein': filename,
        'original_filename': filename,
        'folder': folder_base,
        'name': name,
        'liefernummer': liefernummer,
        'datum': datum
    }
    info_path = os.path.join(folder_path, 'info.json')
    with open(info_path, 'w') as f:
        json.dump(info, f, indent=2, ensure_ascii=False)

    # Decode and save signature PNG
    if ',' in signature_data:
        signature_data = signature_data.split(',', 1)[1]
    img_data = base64.b64decode(signature_data)
    img_path = os.path.join(folder_path, 'Unterschrift.png')
    with open(img_path, 'wb') as f:
        f.write(img_data)

    # Embed signature into PDF
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas as rl_canvas

        reader = PdfReader(original_pdf)
        writer = PdfWriter()
        last_idx = len(reader.pages) - 1

        for i, page in enumerate(reader.pages):
            if i == last_idx:
                pw = float(page.mediabox.width)
                ph = float(page.mediabox.height)
                buf = BytesIO()
                c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

                y_base = 120
                c.setStrokeColorRGB(0.6, 0.6, 0.6)
                c.setLineWidth(0.5)
                c.line(40, y_base + 75, pw - 40, y_base + 75)

                c.setFont('Helvetica-Bold', 10)
                c.drawString(40, y_base + 80, 'Empfangsbestätigung:')

                c.setFont('Helvetica', 10)
                c.drawString(40, y_base + 50, f'Name: {name}')
                c.drawString(40, y_base + 36, f'Datum: {datum}')

                sig_x = pw - 220
                c.drawImage(img_path, sig_x, y_base, width=180, height=70,
                            preserveAspectRatio=True, mask='auto')
                c.setStrokeColorRGB(0, 0, 0)
                c.line(sig_x, y_base - 2, sig_x + 180, y_base - 2)
                c.setFont('Helvetica', 8)
                c.drawString(sig_x, y_base - 12, 'Unterschrift')

                c.save()
                buf.seek(0)
                overlay = PdfReader(buf)
                page.merge_page(overlay.pages[0])

            writer.add_page(page)

        # Save signed PDF into subfolder
        signed_dest = os.path.join(folder_path, 'Bearbeitet.pdf')
        with open(signed_dest, 'wb') as f:
            writer.write(f)

        # Backup original into subfolder
        backup_dest = os.path.join(folder_path, 'Original.pdf')
        shutil.copy2(original_pdf, backup_dest)

        # Remove original from Unbearbeitet
        if os.path.exists(original_pdf):
            os.remove(original_pdf)

    except Exception as e:
        logger.error('PDF embedding failed: %s', e)

    return jsonify({'success': True, 'message': 'Unterschrift gespeichert', 'folder': folder_base})


@app.route('/api/status/<filename>')
def check_status(filename):
    safe = _safe_name(filename)
    config = load_config()
    ba_dir = config.get('bearbeitet_dir', '')
    if not ba_dir or not os.path.isdir(ba_dir):
        return jsonify({'signed': False})

    for entry in os.listdir(ba_dir):
        sub = os.path.join(ba_dir, entry)
        if not os.path.isdir(sub):
            continue
        info_path = os.path.join(sub, 'info.json')
        if os.path.isfile(info_path):
            with open(info_path, 'r') as fh:
                info = json.load(fh)
            if info.get('original_filename') == safe or info.get('folder') == safe:
                return jsonify({'signed': True, 'info': info})

    return jsonify({'signed': False})


@app.route('/api/delete', methods=['POST'])
def delete_signature():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Keine Daten erhalten'}), 400

    filename = _safe_name(data.get('filename', ''))
    if not filename:
        return jsonify({'error': 'Dateiname fehlt'}), 400

    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    if not ub_dir or not ba_dir:
        return jsonify({'error': 'Verzeichnisse nicht konfiguriert'}), 500

    # Find the subfolder containing this document
    info = None
    folder_path = None
    if os.path.isdir(ba_dir):
        for entry in os.listdir(ba_dir):
            sub = os.path.join(ba_dir, entry)
            if not os.path.isdir(sub):
                continue
            info_file = os.path.join(sub, 'info.json')
            if os.path.isfile(info_file):
                with open(info_file, 'r') as fh:
                    candidate = json.load(fh)
                if candidate.get('original_filename') == filename or candidate.get('folder') == filename:
                    info = candidate
                    folder_path = sub
                    break

    if not info or not folder_path:
        return jsonify({'error': 'Keine Unterschrift-Daten gefunden'}), 404

    # Restore original PDF back to Unbearbeitet
    original_filename = info.get('original_filename', filename)
    backup_path = os.path.join(folder_path, 'Original.pdf')
    if os.path.exists(backup_path):
        shutil.move(backup_path, os.path.join(ub_dir, original_filename))

    # Remove entire subfolder
    shutil.rmtree(folder_path, ignore_errors=True)

    return jsonify({'success': True, 'message': 'Unterschrift gelöscht'})


# ──────────────── Admin Routes ────────────────

@app.route('/admin', methods=['GET'])
def admin():
    return redirect(url_for('admin_panel'))


@app.route('/admin/panel')
@admin_required
def admin_panel():
    config = load_config()
    ub_dir = config.get('unbearbeitet_dir', '')
    ba_dir = config.get('bearbeitet_dir', '')
    return render_template(
        'admin.html', config=config,
        ub_count=len(_get_pdfs(ub_dir)),
        ba_count=len(_get_pdfs(ba_dir))
    )


@app.route('/download/<filename>')
def download_pdf(filename):
    config = load_config()
    safe = _safe_name(filename)

    for d in [config.get('bearbeitet_dir', ''), config.get('unbearbeitet_dir', '')]:
        if d and os.path.isdir(d):
            filepath = os.path.join(d, safe)
            if os.path.isfile(filepath) and _is_within(filepath, d):
                return send_file(filepath, as_attachment=True, download_name=safe)

    abort(404)


@app.route('/admin/save', methods=['POST'])
@admin_required
def admin_save():
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
    _start_watcher()  # Restart watcher with new directories

    return jsonify({'success': True, 'message': 'Gespeichert'})


@app.route('/admin/browse')
@admin_required
def admin_browse():
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
    pdf_count = len([f for f in os.listdir(path) if f.lower().endswith('.pdf')])

    return jsonify({'path': path, 'parent': parent, 'folders': folders, 'pdf_count': pdf_count})


@app.route('/admin/browse-native')
@admin_required
def admin_browse_native():
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
@admin_required
def admin_capabilities():
    return jsonify({'native_picker': HAS_NATIVE_PICKER})


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))


# ──────────────── Health & Monitoring ────────────────

@app.route('/health')
def health():
    config = load_config()
    return jsonify({
        'status': 'ok',
        'unbearbeitet_ok': os.path.isdir(config.get('unbearbeitet_dir', '')),
        'bearbeitet_ok': os.path.isdir(config.get('bearbeitet_dir', ''))
    })


# ──────────────── Entry Point ────────────────

if __name__ == '__main__':
    config = load_config()
    os.makedirs(config['unbearbeitet_dir'], exist_ok=True)
    os.makedirs(config['bearbeitet_dir'], exist_ok=True)
    save_config(config)
    _start_watcher()
    socketio.run(
        app, host='0.0.0.0', port=int(os.environ.get('PORT', 5002)),
        debug=os.environ.get('FLASK_DEBUG', '0') == '1',
        allow_unsafe_werkzeug=os.environ.get('FLASK_DEBUG', '0') == '1'
    )
