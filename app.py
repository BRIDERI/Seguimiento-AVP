from flask import Flask, render_template_string, request, jsonify
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "seguimiento.db")
API_KEY = os.environ.get("API_KEY", "cambiar-esta-clave")


# ============================================================
# BASE DE DATOS
# ============================================================

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

    # Migración segura si la tabla ya existía antes.
    ensure_column(conn, "actividades", "responsable_nombre", "TEXT")
    ensure_column(conn, "actividades", "avance", "TEXT")

    conn.commit()
    conn.close()


init_db()


def validar_api(req):
    return req.headers.get("X-API-KEY", "") == API_KEY


def fecha_hora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def valor(row, campo, default=""):
    try:
        v = row[campo]
        return default if v is None else v
    except Exception:
        return default


# ============================================================
# HTML
# ============================================================

CSS_BASE = """
<style>
    :root {
        --azul:#003B79;
        --azul2:#005BAA;
        --gris:#f4f6f8;
        --borde:#d9e1ea;
        --texto:#1f2933;
        --verde:#1f9d55;
        --ambar:#f2b600;
    }

    body {
        margin:0;
        background:#f2f4f7;
        font-family: Arial, Helvetica, sans-serif;
        color:var(--texto);
    }

    .card {
        max-width:760px;
        margin:48px auto;
        background:#fff;
        border-radius:14px;
        box-shadow:0 12px 30px rgba(0,0,0,.08);
        padding:30px 34px;
    }

    h1 {
        color:var(--azul);
        font-size:24px;
        margin:0 0 4px 0;
        letter-spacing:.2px;
    }

    .subtitulo {
        color:#555;
        font-size:14px;
        margin-bottom:24px;
    }

    .intro {
        line-height:1.5;
        margin-bottom:20px;
    }

    .tabla {
        width:100%;
        border-collapse:collapse;
        margin:18px 0 24px 0;
        border:1px solid var(--borde);
        border-radius:10px;
        overflow:hidden;
    }

    .tabla td {
        padding:12px 14px;
        border-bottom:1px solid var(--borde);
        vertical-align:top;
        font-size:14px;
    }

    .tabla tr:last-child td {
        border-bottom:none;
    }

    .label {
        width:190px;
        color:var(--azul);
        font-weight:700;
        background:#fbfcfe;
    }

    .actividad {
        color:var(--azul);
        font-weight:700;
        text-transform:uppercase;
        letter-spacing:.3px;
    }

    .acciones {
        background:#f5f7fa;
    }

    .bloque-respuesta {
        text-align:center;
        margin:22px 0 18px 0;
    }

    .acciones-botones {
        display:flex;
        gap:16px;
        flex-wrap:wrap;
        margin-top:18px;
    }

    button {
        border:0;
        border-radius:8px;
        padding:13px 20px;
        font-weight:700;
        cursor:pointer;
        font-size:15px;
    }

    .btn-verde {
        background:var(--verde);
        color:white;
    }

    .btn-ambar {
        background:var(--ambar);
        color:#111;
    }

    .form-reprog {
        margin-top:24px;
        border-top:1px solid var(--borde);
        padding-top:20px;
    }

    input, textarea {
        width:100%;
        box-sizing:border-box;
        border:1px solid #cfd8e3;
        border-radius:8px;
        padding:11px 12px;
        font-size:14px;
        margin-top:6px;
        font-family: Arial, Helvetica, sans-serif;
    }

    textarea {
        min-height:80px;
        resize:vertical;
    }

    .campo {
        margin-bottom:14px;
    }

    .nota {
        color:#56616f;
        font-size:13px;
        margin-top:18px;
    }

    .ok {
        color:#138a43;
    }

    .warn {
        color:#b00020;
    }

    .footer {
        margin-top:24px;
        font-size:12px;
        color:#667085;
        border-top:1px solid #e5e7eb;
        padding-top:14px;
    }
</style>
"""

TPL_NO_ENCONTRADO = CSS_BASE + """
<div class="card">
    <h1 class="warn">Actividad no encontrada</h1>
    <p>El enlace no es válido o la actividad ya no existe en la base temporal del sistema.</p>
</div>
"""

TPL_YA_REGISTRADO = CSS_BASE + """
<div class="card">
    <h1 class="ok">Respuesta ya registrada</h1>
    <p>Esta actividad ya cuenta con una respuesta registrada.</p>

    <table class="tabla">
        <tr><td class="label">Responsable</td><td>{{ responsable_nombre }}</td></tr>
        <tr><td class="label">Actividad</td><td class="actividad">{{ a['actividad'] }}</td></tr>
        <tr><td class="label">Estado</td><td>{{ a['estado'] }}</td></tr>
        <tr><td class="label">Fecha de respuesta</td><td>{{ a['fecha_respuesta'] }}</td></tr>
        {% if a['nueva_fecha'] %}
        <tr><td class="label">Nueva fecha</td><td>{{ a['nueva_fecha'] }}</td></tr>
        {% endif %}
        {% if a['avance'] %}
        <tr><td class="label">Avance informado</td><td>{{ a['avance'] }}%</td></tr>
        {% endif %}
        {% if a['comentario'] %}
        <tr><td class="label">Comentario</td><td>{{ a['comentario'] }}</td></tr>
        {% endif %}
    </table>
</div>
"""

TPL_REGISTRADO = CSS_BASE + """
<div class="card">
    <h1 class="ok">Respuesta registrada</h1>
    <p>Gracias. La respuesta fue registrada correctamente.</p>

    <table class="tabla">
        <tr><td class="label">Responsable</td><td>{{ responsable_nombre }}</td></tr>
        <tr><td class="label">Actividad</td><td class="actividad">{{ a['actividad'] }}</td></tr>
        <tr><td class="label">Estado</td><td>{{ a['estado'] }}</td></tr>
        <tr><td class="label">Fecha de respuesta</td><td>{{ a['fecha_respuesta'] }}</td></tr>
        {% if a['nueva_fecha'] %}
        <tr><td class="label">Nueva fecha</td><td>{{ a['nueva_fecha'] }}</td></tr>
        {% endif %}
        {% if a['avance'] %}
        <tr><td class="label">Avance informado</td><td>{{ a['avance'] }}%</td></tr>
        {% endif %}
        {% if a['comentario'] %}
        <tr><td class="label">Comentario</td><td>{{ a['comentario'] }}</td></tr>
        {% endif %}
    </table>
</div>
"""

TPL_ACTIVIDAD = CSS_BASE + """
<div class="card">
    <h1>Seguimiento de actividad</h1>
    <div class="subtitulo">Proyecto Anillo Vial Periférico</div>

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
            <td class="label">Fecha programada final</td>
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
        <div class="bloque-respuesta">
            <button class="btn-verde" type="submit" name="estado" value="Culminado">
                ✓ Culminado
            </button>
        </div>

        <div class="form-reprog">
            <div class="campo">
                <b>Nueva fecha si desea reprogramar:</b>
                <input type="date" name="nueva_fecha">
            </div>

            <div class="campo">
                <b>Porcentaje de avance:</b>
                <input type="number" name="avance" min="0" max="100" placeholder="Ejemplo: 80">
            </div>

            <div class="campo">
                <b>Comentario:</b>
                <textarea name="comentario" placeholder="Motivo o comentario breve"></textarea>
            </div>

            <button class="btn-ambar" type="submit" name="estado" value="Reprogramar">
                ↻ Reprogramar
            </button>
        </div>
    </form>

    <div class="footer">
        Solo se acepta una respuesta por actividad.
    </div>
</div>
"""


# ============================================================
# RUTAS
# ============================================================

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

    responsable_nombre = valor(row, "responsable_nombre") or valor(row, "responsable") or "-"

    if row["respondido"] == 1:
        return render_template_string(
            TPL_YA_REGISTRADO,
            a=row,
            responsable_nombre=responsable_nombre
        )

    return render_template_string(
        TPL_ACTIVIDAD,
        a=row,
        responsable_nombre=responsable_nombre
    )


@app.route("/registrar/<token>", methods=["POST"])
def registrar(token):
    estado = request.form.get("estado", "").strip()
    canal = request.form.get("canal", "Link")
    nueva_fecha = request.form.get("nueva_fecha") or ""
    avance = request.form.get("avance") or ""
    comentario = request.form.get("comentario") or ""
    fecha_respuesta = fecha_hora()

    if estado == "Culminado":
        avance = "100"
        nueva_fecha = ""

    conn = get_conn()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()

    if row is None:
        conn.close()
        return render_template_string(TPL_NO_ENCONTRADO)

    if row["respondido"] == 1:
        responsable_nombre = valor(row, "responsable_nombre") or valor(row, "responsable") or "-"
        conn.close()
        return render_template_string(
            TPL_YA_REGISTRADO,
            a=row,
            responsable_nombre=responsable_nombre
        )

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
    """, (estado, canal, fecha_respuesta, nueva_fecha, avance, comentario, token))

    conn.commit()

    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()
    conn.close()

    responsable_nombre = valor(row, "responsable_nombre") or valor(row, "responsable") or "-"

    return render_template_string(
        TPL_REGISTRADO,
        a=row,
        responsable_nombre=responsable_nombre
    )


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
            token,
            campos["proyecto"],
            campos["hoja"],
            campos["grupo"],
            campos["item"],
            campos["responsable"],
            campos["responsable_nombre"],
            campos["email"],
            campos["telefono"],
            campos["actividad"],
            campos["fecha_inicio"],
            campos["fecha_programada"],
            campos["proximas_acciones"],
        ))
        accion = "insertado"
    else:
        if existe["respondido"] == 0:
            conn.execute("""
                UPDATE actividades
                SET proyecto = ?,
                    hoja = ?,
                    grupo = ?,
                    item = ?,
                    responsable = ?,
                    responsable_nombre = ?,
                    email = ?,
                    telefono = ?,
                    actividad = ?,
                    fecha_inicio = ?,
                    fecha_programada = ?,
                    proximas_acciones = ?
                WHERE token = ?
            """, (
                campos["proyecto"],
                campos["hoja"],
                campos["grupo"],
                campos["item"],
                campos["responsable"],
                campos["responsable_nombre"],
                campos["email"],
                campos["telefono"],
                campos["actividad"],
                campos["fecha_inicio"],
                campos["fecha_programada"],
                campos["proximas_acciones"],
                token,
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

