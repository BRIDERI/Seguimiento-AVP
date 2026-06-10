from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)
DB_PATH = os.environ.get('DB_PATH', 'seguimiento.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS actividades (
            token TEXT PRIMARY KEY,
            item TEXT,
            responsable TEXT,
            email TEXT,
            telefono TEXT,
            actividad TEXT,
            fecha_programada TEXT,
            proximas_acciones TEXT,
            respondido INTEGER DEFAULT 0,
            estado TEXT,
            canal TEXT,
            fecha_respuesta TEXT,
            nueva_fecha TEXT,
            comentario TEXT
        )
    ''')
    conn.commit()
    conn.close()


@app.route('/')
def index():
    return 'Sistema de seguimiento AVP activo.'


@app.route('/r/<token>')
def ver_actividad(token):
    conn = get_conn()
    row = conn.execute('SELECT * FROM actividades WHERE token = ?', (token,)).fetchone()
    conn.close()
    if row is None:
        return render_template('no_encontrado.html')
    return render_template('actividad.html', a=row)


@app.route('/registrar/<token>', methods=['POST'])
def registrar(token):
    estado = request.form.get('estado')
    canal = request.form.get('canal', 'Link')
    nueva_fecha = request.form.get('nueva_fecha') or ''
    comentario = request.form.get('comentario') or ''
    fecha_respuesta = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_conn()
    row = conn.execute('SELECT * FROM actividades WHERE token = ?', (token,)).fetchone()
    if row is None:
        conn.close()
        return render_template('no_encontrado.html')

    if row['respondido'] == 1:
        conn.close()
        return render_template('ya_registrado.html', a=row)

    conn.execute('''
        UPDATE actividades
        SET respondido = 1,
            estado = ?,
            canal = ?,
            fecha_respuesta = ?,
            nueva_fecha = ?,
            comentario = ?
        WHERE token = ?
    ''', (estado, canal, fecha_respuesta, nueva_fecha, comentario, token))
    conn.commit()

    row = conn.execute('SELECT * FROM actividades WHERE token = ?', (token,)).fetchone()
    conn.close()
    return render_template('registrado.html', a=row)


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
