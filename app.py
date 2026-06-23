from flask import Flask, render_template_string, request, jsonify
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "seguimiento.db")
API_KEY = os.environ.get("API_KEY", "cambiar-esta-clave")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actividades (
            token TEXT PRIMARY KEY,
            proyecto TEXT,
            hoja TEXT,
            grupo TEXT,
            item TEXT,
            responsable TEXT,
            responsable_nombre TEXT,
            email TEXT,
            telefono TEXT,
            actividad TEXT,
            fecha_inicio TEXT,
            fecha_programada TEXT,
            proximas_acciones TEXT,
            respondido INTEGER DEFAULT 0,
            estado TEXT,
            canal TEXT,
            fecha_respuesta TEXT,
            nueva_fecha TEXT,
            avance TEXT,
            comentario TEXT
        )
    """)
    ensure_column(conn, "actividades", "responsable_nombre", "TEXT")
    ensure_column(conn, "actividades", "avance", "TEXT")
    conn.commit()
    conn.close()


init_db()


def validar_api(req):
    return req.headers.get("X-API-KEY", "") == API_KEY


def ahora_txt():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def v(row, campo, default=""):
    try:
        valor = row[campo]
        return default if valor is None else valor
    except Exception:
        return default


def mostrar_nombre(row):
    return v(row, "responsable_nombre") or v(row, "responsable") or "-"


CSS = """
<style>
    :root{
        --azul:#003B79;
        --azul2:#004C97;
        --gris:#f4f6f8;
        --borde:#dce3ec;
        --texto:#1f2933;
        --verde:#16a05d;
        --ambar:#f3b400;
    }
    body{
        margin:0;
        background:#f3f5f8;
        font-family:Arial, Helvetica, sans-serif;
        color:var(--texto);
    }
    .card{
        max-width:620px;
        margin:42px auto;
        background:white;
        border-radius:14px;
        box-shadow:0 10px 26px rgba(0,0,0,.08);
        padding:26px 30px;
    }
    h1{
        margin:0 0 4px 0;
        color:var(--azul);
        font-size:22px;
    }
    .sub{
        font-size:13px;
        color:#667085;
        margin-bottom:18px;
    }
    .intro{
        font-size:15px;
        line-height:1.45;
        margin:0 0 16px 0;
    }
    .tabla{
        width:100%;
        border-collapse:collapse;
        margin:14px 0 18px 0;
        border:1px solid var(--borde);
        border-radius:10px;
        overflow:hidden;
    }
    .tabla td{
        padding:10px 12px;
        border-bottom:1px solid var(--borde);
        font-size:14px;
        vertical-align:top;
    }
    .tabla tr:last-child td{border-bottom:none;}
    .label{
        width:155px;
        color:var(--azul);
        font-weight:700;
        background:#fbfcfe;
    }
    .actividad{
        color:var(--azul);
        font-weight:700;
        text-transform:uppercase;
        letter-spacing:.2px;
    }
    .acciones{
        background:#f5f7fa;
    }
    .botones{
        display:flex;
        gap:12px;
        align-items:center;
        justify-content:center;
        margin:18px 0 8px 0;
        flex-wrap:wrap;
    }
    button{
        border:0;
        border-radius:8px;
        padding:11px 18px;
        font-weight:700;
        cursor:pointer;
        font-size:14px;
    }
    .btn-ok{background:var(--verde); color:white;}
    .btn-toggle{background:var(--ambar); color:#111;}
    .form-reprog{
        display:none;
        margin-top:14px;
        border-top:1px solid var(--borde);
        padding-top:16px;
    }
    .campo{margin-bottom:11px;}
    label{
        font-weight:700;
        font-size:14px;
    }
    input, textarea{
        width:100%;
        box-sizing:border-box;
        border:1px solid #cfd8e3;
        border-radius:8px;
        padding:9px 10px;
        font-size:14px;
        margin-top:5px;
        font-family:Arial, Helvetica, sans-serif;
    }
    textarea{min-height:64px; resize:vertical;}
    .btn-reprog{background:var(--ambar); color:#111;}
    .nota{
        margin-top:16px;
        padding-top:12px;
        border-top:1px solid #edf0f3;
        font-size:12px;
        color:#667085;
    }
    .ok{color:#138a43;}
    .warn{color:#b00020;}
</style>
"""

TPL_NO_ENCONTRADO = CSS + """
<div class="card">
    <h1 class="warn">Actividad no encontrada</h1>
    <p>El enlace no es válido o la actividad ya no está cargada en la base temporal del sistema.</p>
</div>
"""

TPL_ACTIVIDAD = CSS + """
<div class="card">
    <h1>Seguimiento de actividad</h1>
    <div class="sub">Proyecto Anillo Vial Periférico</div>

    <p class="intro">
        Estimado(a) <b>{{ responsable_nombre }}</b>,<br>
        registre el estado de la siguiente actividad:
    </p>

    <table class="tabla">
        <tr>
            <td class="label">Intervención</td>
            <td>{{ a['proyecto'] or '-' }}</td>
        </tr>
        {% if a['grupo'] %}
        <tr>
            <td class="label">Etapa / Grupo</td>
            <td>{{ a['grupo'] }}</td>
        </tr>
        {% endif %}
        <tr>
            <td class="label">Actividad</td>
            <td class="actividad">{{ a['actividad'] }}</td>
        </tr>
        <tr>
            <td class="label">Fecha final</td>
            <td>{{ a['fecha_programada'] }}</td>
        </tr>
        {% if a['proximas_acciones'] %}
        <tr>
            <td class="label">Próximas acciones</td>
            <td class="acciones">{{ a['proximas_acciones'] }}</td>
        </tr>
        {% endif %}
    </table>

    <form action="/registrar/{{ a['token'] }}" method="POST">
        <div class="botones">
            <button class="btn-ok" type="submit" name="estado" value="Culminado">✓ Culminado</button>
            <button class="btn-toggle" type="button" onclick="document.getElementById('reprog').style.display='block'; this.style.display='none';">↻ Reprogramar</button>
        </div>

        <div id="reprog" class="form-reprog">
            <div class="campo">
                <label>Nueva fecha:</label>
                <input type="date" name="nueva_fecha">
            </div>
            <div class="campo">
                <label>Porcentaje de avance:</label>
                <input type="number" name="avance" min="0" max="100" placeholder="Ejemplo: 80">
            </div>
            <div class="campo">
                <label>Comentario:</label>
                <textarea name="comentario" placeholder="Motivo o comentario breve"></textarea>
            </div>
            <button class="btn-reprog" type="submit" name="estado" value="Reprogramar">Guardar reprogramación</button>
        </div>
    </form>

    <div class="nota">Solo se acepta una respuesta por actividad.</div>
</div>
"""

TPL_REGISTRADO = CSS + """
<div class="card">
    <h1 class="ok">Respuesta registrada</h1>
    <div class="sub">Proyecto Anillo Vial Periférico</div>

    <table class="tabla">
        <tr><td class="label">Responsable</td><td>{{ responsable_nombre }}</td></tr>
        <tr><td class="label">Actividad</td><td class="actividad">{{ a['actividad'] }}</td></tr>
        <tr><td class="label">Estado</td><td>{{ a['estado'] }}</td></tr>
        {% if a['avance'] %}
        <tr><td class="label">Avance</td><td>{{ a['avance'] }}%</td></tr>
        {% endif %}
        {% if a['nueva_fecha'] %}
        <tr><td class="label">Nueva fecha</td><td>{{ a['nueva_fecha'] }}</td></tr>
        {% endif %}
        {% if a['comentario'] %}
        <tr><td class="label">Comentario</td><td>{{ a['comentario'] }}</td></tr>
        {% endif %}
        <tr><td class="label">Fecha de respuesta</td><td>{{ a['fecha_respuesta'] }}</td></tr>
    </table>
</div>
"""


@app.route("/")
def index():
    return "Sistema de seguimiento AVP activo."


@app.route("/r/<token>")
def ver_actividad(token):
    conn = get_conn()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()
    conn.close()

    if row is None:
        return render_template_string(TPL_NO_ENCONTRADO)

    responsable_nombre = mostrar_nombre(row)

    if row["respondido"] == 1:
        return render_template_string(TPL_REGISTRADO, a=row, responsable_nombre=responsable_nombre)

    return render_template_string(TPL_ACTIVIDAD, a=row, responsable_nombre=responsable_nombre)


@app.route("/registrar/<token>", methods=["POST"])
def registrar(token):
    estado = request.form.get("estado", "").strip()
    canal = request.form.get("canal", "Link")
    nueva_fecha = request.form.get("nueva_fecha") or ""
    avance = request.form.get("avance") or ""
    comentario = request.form.get("comentario") or ""

    if estado == "Culminado":
        avance = "100"
        nueva_fecha = ""
        comentario = comentario or ""

    conn = get_conn()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()

    if row is None:
        conn.close()
        return render_template_string(TPL_NO_ENCONTRADO)

    if row["respondido"] == 1:
        responsable_nombre = mostrar_nombre(row)
        conn.close()
        return render_template_string(TPL_REGISTRADO, a=row, responsable_nombre=responsable_nombre)

    conn.execute("""
        UPDATE actividades
        SET respondido = 1,
            estado = ?,
            canal = ?,
            fecha_respuesta = ?,
            nueva_fecha = ?,
            avance = ?,
            comentario = ?
        WHERE token = ?
    """, (estado, canal, ahora_txt(), nueva_fecha, avance, comentario, token))

    conn.commit()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()
    conn.close()

    return render_template_string(TPL_REGISTRADO, a=row, responsable_nombre=mostrar_nombre(row))


@app.route("/api/actividad", methods=["POST"])
def api_actividad():
    if not validar_api(request):
        return jsonify({"ok": False, "error": "No autorizado"}), 401

    data = request.get_json(force=True)
    token = data.get("token")
    if not token:
        return jsonify({"ok": False, "error": "Falta token"}), 400

    campos = {
        "proyecto": data.get("proyecto", ""),
        "hoja": data.get("hoja", ""),
        "grupo": data.get("grupo", ""),
        "item": data.get("item", ""),
        "responsable": data.get("responsable", ""),
        "responsable_nombre": data.get("responsable_nombre", "") or data.get("responsable", ""),
        "email": data.get("email", ""),
        "telefono": data.get("telefono", ""),
        "actividad": data.get("actividad", ""),
        "fecha_inicio": data.get("fecha_inicio", ""),
        "fecha_programada": data.get("fecha_programada", ""),
        "proximas_acciones": data.get("proximas_acciones", ""),
    }

    conn = get_conn()
    existe = conn.execute("SELECT respondido FROM actividades WHERE token = ?", (token,)).fetchone()

    if existe is None:
        conn.execute("""
            INSERT INTO actividades (
                token, proyecto, hoja, grupo, item, responsable, responsable_nombre,
                email, telefono, actividad, fecha_inicio, fecha_programada,
                proximas_acciones, respondido
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            token, campos["proyecto"], campos["hoja"], campos["grupo"], campos["item"],
            campos["responsable"], campos["responsable_nombre"], campos["email"],
            campos["telefono"], campos["actividad"], campos["fecha_inicio"],
            campos["fecha_programada"], campos["proximas_acciones"]
        ))
        accion = "insertado"
    else:
        if existe["respondido"] == 0:
            conn.execute("""
                UPDATE actividades
                SET proyecto = ?, hoja = ?, grupo = ?, item = ?, responsable = ?,
                    responsable_nombre = ?, email = ?, telefono = ?, actividad = ?,
                    fecha_inicio = ?, fecha_programada = ?, proximas_acciones = ?
                WHERE token = ?
            """, (
                campos["proyecto"], campos["hoja"], campos["grupo"], campos["item"],
                campos["responsable"], campos["responsable_nombre"], campos["email"],
                campos["telefono"], campos["actividad"], campos["fecha_inicio"],
                campos["fecha_programada"], campos["proximas_acciones"], token
            ))
            accion = "actualizado"
        else:
            accion = "ya_respondido_no_modificado"

    conn.commit()
    conn.close()

    return jsonify({"ok": True, "accion": accion, "token": token})


@app.route("/api/respuestas", methods=["GET"])
def api_respuestas():
    if not validar_api(request):
        return jsonify({"ok": False, "error": "No autorizado"}), 401

    conn = get_conn()
    rows = conn.execute("""
        SELECT token, proyecto, hoja, grupo, item, responsable, responsable_nombre,
               email, telefono, actividad, fecha_inicio, fecha_programada,
               proximas_acciones, respondido, estado, canal, fecha_respuesta,
               nueva_fecha, avance, comentario
        FROM actividades
        ORDER BY fecha_programada, proyecto, grupo, actividad
    """).fetchall()
    conn.close()

    return jsonify({"ok": True, "total": len(rows), "data": [dict(r) for r in rows]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
