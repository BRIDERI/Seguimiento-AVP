from flask import Flask, render_template_string, request, jsonify
import sqlite3
from datetime import datetime
import os, json, base64
import requests

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "seguimiento.db")
API_KEY = os.environ.get("API_KEY", "cambiar-esta-clave")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "BRIDERI")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Seguimiento-AVP")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

GITHUB_ACTIVIDADES_DIR = "data/actividades"
GITHUB_RESPUESTAS_DIR = "data/respuestas"


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
            id_actividad TEXT,
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
    ensure_column(conn, "actividades", "id_actividad", "TEXT")
    conn.commit()
    conn.close()


init_db()


def validar_api(req):
    return req.headers.get("X-API-KEY", "") == API_KEY


def ahora_txt():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def getv(obj, key, default=""):
    if obj is None:
        return default
    if isinstance(obj, dict):
        v = obj.get(key, default)
    else:
        try:
            v = obj[key]
        except Exception:
            v = default
    return default if v is None else v


def mostrar_nombre(obj):
    return getv(obj, "responsable_nombre") or getv(obj, "responsable") or "-"


def estado_norm(estado):
    return str(estado or "").strip().upper()


def es_reprogramar(estado):
    return estado_norm(estado) == "REPROGRAMAR"


def es_culminado(estado):
    return estado_norm(estado) == "CULMINADO"


def debe_reemplazar_respuesta(estado_actual, estado_nuevo):
    """
    Regla de consolidación menos favorable:
    - Reprogramar prevalece sobre Culminado.
    - Culminado NO reemplaza una Reprogramación.
    - Reprogramar sí reemplaza un Culminado.
    - Si ambas son Reprogramar, se actualiza conservando el menor avance.
    """
    if not estado_actual:
        return True

    if es_reprogramar(estado_actual) and es_culminado(estado_nuevo):
        return False

    if es_culminado(estado_actual) and es_reprogramar(estado_nuevo):
        return True

    if es_reprogramar(estado_actual) and es_reprogramar(estado_nuevo):
        return True

    if es_culminado(estado_actual) and es_culminado(estado_nuevo):
        return False

    return True


def menor_avance(avance_actual, avance_nuevo):
    """
    Para respuestas múltiples en Reprogramar, conserva el menor avance.
    """
    def conv(x):
        try:
            if x is None or str(x).strip() == "":
                return None
            return float(str(x).replace("%", "").replace(",", "."))
        except Exception:
            return None

    a = conv(avance_actual)
    b = conv(avance_nuevo)

    if a is None:
        return avance_nuevo
    if b is None:
        return avance_actual

    menor = min(a, b)
    if menor.is_integer():
        return str(int(menor))
    return str(menor)


def github_enabled():
    return bool(GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO and GITHUB_BRANCH)


def gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gh_url(path):
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"


def gh_get_json(path):
    if not github_enabled():
        return None, None

    r = requests.get(gh_url(path), headers=gh_headers(), params={"ref": GITHUB_BRANCH}, timeout=30)
    if r.status_code == 404:
        return None, None

    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data.get("sha")


def gh_put_json(path, data, message):
    if not github_enabled():
        return {"ok": False, "skipped": True, "error": "GitHub no configurado"}

    _old, sha = gh_get_json(path)
    content = json.dumps(data, ensure_ascii=False, indent=2)

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(gh_url(path), headers=gh_headers(), data=json.dumps(payload), timeout=30)
    r.raise_for_status()
    return {"ok": True, "path": path}


def path_actividad(token):
    return f"{GITHUB_ACTIVIDADES_DIR}/{token}.json"


def path_respuesta(token):
    return f"{GITHUB_RESPUESTAS_DIR}/{token}.json"


def guardar_actividad_github(data):
    token = data.get("token", "")
    if token:
        return gh_put_json(path_actividad(token), data, f"Guardar actividad {token}")
    return {"ok": False, "error": "sin token"}


def guardar_respuesta_github(data):
    token = data.get("token", "")
    if token:
        return gh_put_json(path_respuesta(token), data, f"Guardar respuesta {token}")
    return {"ok": False, "error": "sin token"}


def recuperar_actividad_github(token):
    data, _sha = gh_get_json(path_actividad(token))
    return data


def recuperar_respuesta_github(token):
    data, _sha = gh_get_json(path_respuesta(token))
    return data


def insertar_o_actualizar_local(data, conservar_si_respondido=True):
    token = data.get("token")
    if not token:
        return "sin_token"

    campos = {
        "proyecto": data.get("proyecto", ""),
        "hoja": data.get("hoja", ""),
        "grupo": data.get("grupo", ""),
        "item": data.get("item", ""),
        "id_actividad": data.get("id_actividad", ""),
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
                token, proyecto, hoja, grupo, item, id_actividad, responsable, responsable_nombre,
                email, telefono, actividad, fecha_inicio, fecha_programada,
                proximas_acciones, respondido
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            token, campos["proyecto"], campos["hoja"], campos["grupo"], campos["item"],
            campos["id_actividad"], campos["responsable"], campos["responsable_nombre"],
            campos["email"], campos["telefono"], campos["actividad"], campos["fecha_inicio"],
            campos["fecha_programada"], campos["proximas_acciones"]
        ))
        accion = "insertado"
    else:
        if conservar_si_respondido and existe["respondido"] == 1:
            accion = "ya_respondido_no_modificado"
        else:
            conn.execute("""
                UPDATE actividades
                SET proyecto = ?, hoja = ?, grupo = ?, item = ?, id_actividad = ?, responsable = ?,
                    responsable_nombre = ?, email = ?, telefono = ?, actividad = ?,
                    fecha_inicio = ?, fecha_programada = ?, proximas_acciones = ?
                WHERE token = ?
            """, (
                campos["proyecto"], campos["hoja"], campos["grupo"], campos["item"],
                campos["id_actividad"], campos["responsable"], campos["responsable_nombre"],
                campos["email"], campos["telefono"], campos["actividad"], campos["fecha_inicio"],
                campos["fecha_programada"], campos["proximas_acciones"], token
            ))
            accion = "actualizado"

    conn.commit()
    conn.close()
    return accion


def guardar_respuesta_local(token, estado_nuevo, canal, nueva_fecha, avance_nuevo, status):
    """
    Guarda respuesta aplicando la regla de menor favorabilidad.
    El campo visible se llama STATUS, pero internamente se conserva en 'comentario'
    para no romper la base anterior ni los JSON existentes.
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()

    if row is None:
        conn.close()
        return None, "no_encontrado"

    estado_actual = row["estado"]
    respondido_actual = row["respondido"]

    if respondido_actual == 1 and not debe_reemplazar_respuesta(estado_actual, estado_nuevo):
        conn.close()
        return row, "mantiene_respuesta_menos_favorable"

    avance_final = avance_nuevo
    nueva_fecha_final = nueva_fecha
    status_final = status

    if es_reprogramar(estado_actual) and es_reprogramar(estado_nuevo):
        avance_final = menor_avance(row["avance"], avance_nuevo)

        if not status_final:
            status_final = row["comentario"] or ""

        if not nueva_fecha_final:
            nueva_fecha_final = row["nueva_fecha"] or ""

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
    """, (estado_nuevo, canal, ahora_txt(), nueva_fecha_final, avance_final, status_final, token))

    conn.commit()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()
    conn.close()
    return row, "actualizado"


def obtener_local(token):
    conn = get_conn()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()
    conn.close()
    return row


def obtener_actividad_o_respuesta(token):
    row = obtener_local(token)
    if row is not None:
        return row, "local"

    respuesta = recuperar_respuesta_github(token)
    if respuesta:
        return respuesta, "github_respuesta"

    actividad = recuperar_actividad_github(token)
    if actividad:
        insertar_o_actualizar_local(actividad, conservar_si_respondido=False)
        row = obtener_local(token)
        return row, "github_actividad"

    return None, "no_encontrado"


CSS = """
<style>
:root{--azul:#003B79;--borde:#dce3ec;--texto:#1f2933;--verde:#16a05d;--ambar:#f3b400;}
body{margin:0;background:#f3f5f8;font-family:Arial,Helvetica,sans-serif;color:var(--texto);}
.card{max-width:620px;margin:42px auto;background:white;border-radius:14px;box-shadow:0 10px 26px rgba(0,0,0,.08);padding:26px 30px;}
h1{margin:0 0 4px 0;color:var(--azul);font-size:22px;}
.sub{font-size:13px;color:#667085;margin-bottom:18px;}
.intro{font-size:15px;line-height:1.45;margin:0 0 16px 0;}
.tabla{width:100%;border-collapse:collapse;margin:14px 0 18px 0;border:1px solid var(--borde);border-radius:10px;overflow:hidden;}
.tabla td{padding:10px 12px;border-bottom:1px solid var(--borde);font-size:14px;vertical-align:top;}
.tabla tr:last-child td{border-bottom:none;}
.label{width:155px;color:var(--azul);font-weight:700;background:#fbfcfe;}
.actividad{color:var(--azul);font-weight:700;text-transform:uppercase;letter-spacing:.2px;}
.acciones{background:#f5f7fa;}
.botones{display:flex;gap:12px;align-items:center;justify-content:center;margin:18px 0 8px 0;flex-wrap:wrap;}
button{border:0;border-radius:8px;padding:11px 18px;font-weight:700;cursor:pointer;font-size:14px;}
.btn-ok{background:var(--verde);color:white;}
.btn-toggle{background:var(--ambar);color:#111;}
.form-reprog{display:none;margin-top:14px;border-top:1px solid var(--borde);padding-top:16px;}
.campo{margin-bottom:11px;}
label{font-weight:700;font-size:14px;}
input,textarea{width:100%;box-sizing:border-box;border:1px solid #cfd8e3;border-radius:8px;padding:9px 10px;font-size:14px;margin-top:5px;font-family:Arial,Helvetica,sans-serif;}
textarea{min-height:64px;resize:vertical;}
.btn-reprog{background:var(--ambar);color:#111;}
.nota{margin-top:16px;padding-top:12px;border-top:1px solid #edf0f3;font-size:12px;color:#667085;}
.ok{color:#138a43;}
.warn{color:#b00020;}
</style>
"""

TPL_NO_ENCONTRADO = CSS + """
<div class="card">
    <h1 class="warn">Registro no disponible</h1>
    <div class="sub">Proyecto Anillo Vial Periférico</div>
    <p>Esta actividad no está disponible en la base temporal ni en el respaldo GitHub.</p>
    <p class="nota">Revise si existen las carpetas <b>data/actividades</b> o <b>data/respuestas</b> en GitHub.</p>
</div>
"""

TPL_ACTIVIDAD = CSS + """
<div class="card">
    <h1>Seguimiento de actividad</h1>
    <div class="sub">Proyecto Anillo Vial Periférico</div>
    <p class="intro">Estimado(a) <b>{{ responsable_nombre }}</b>,<br>registre el estado de la siguiente actividad:</p>
    <table class="tabla">
        <tr><td class="label">Intervención</td><td>{{ a['proyecto'] or '-' }}</td></tr>
        {% if a['grupo'] %}<tr><td class="label">Etapa / Grupo</td><td>{{ a['grupo'] }}</td></tr>{% endif %}
        <tr><td class="label">Actividad</td><td class="actividad">{{ a['actividad'] }}</td></tr>
        <tr><td class="label">Fecha final</td><td>{{ a['fecha_programada'] }}</td></tr>
        {% if a['proximas_acciones'] %}<tr><td class="label">Próximas acciones</td><td class="acciones">{{ a['proximas_acciones'] }}</td></tr>{% endif %}
    </table>
    <form action="/registrar/{{ a['token'] }}" method="POST">
        <div class="botones">
            <button class="btn-ok" type="submit" name="estado" value="Culminado">✓ Culminado</button>
            <button class="btn-toggle" type="button" onclick="document.getElementById('reprog').style.display='block'; this.style.display='none';">↻ Reprogramar</button>
        </div>
        <div id="reprog" class="form-reprog">
            <div class="campo"><label>Nueva fecha:</label><input type="date" name="nueva_fecha"></div>
            <div class="campo"><label>Porcentaje de avance:</label><input type="number" name="avance" min="0" max="100" placeholder="Ejemplo: 80"></div>
            <div class="campo"><label>Status:</label><textarea name="comentario" placeholder="Indique el status actual de la actividad"></textarea></div>
            <button class="btn-reprog" type="submit" name="estado" value="Reprogramar">Guardar reprogramación</button>
        </div>
    </form>
    <div class="nota">Si la actividad tiene más de un responsable, se conservará la respuesta menos favorable.</div>
</div>
"""

TPL_REGISTRADO = CSS + """
<div class="card">
    <h1 class="ok">Respuesta registrada</h1>
    <div class="sub">Proyecto Anillo Vial Periférico</div>
    <table class="tabla">
        <tr><td class="label">Responsable</td><td>{{ responsable_nombre }}</td></tr>
        <tr><td class="label">Actividad</td><td class="actividad">{{ a['actividad'] }}</td></tr>
        <tr><td class="label">Estado consolidado</td><td>{{ a['estado'] }}</td></tr>
        {% if a['avance'] %}<tr><td class="label">Avance</td><td>{{ a['avance'] }}%</td></tr>{% endif %}
        {% if a['nueva_fecha'] %}<tr><td class="label">Nueva fecha</td><td>{{ a['nueva_fecha'] }}</td></tr>{% endif %}
        {% if a['comentario'] %}<tr><td class="label">Status</td><td>{{ a['comentario'] }}</td></tr>{% endif %}
        <tr><td class="label">Fecha de respuesta</td><td>{{ a['fecha_respuesta'] }}</td></tr>
    </table>
    <div class="nota">Esta actividad ya cuenta con respuesta registrada. Si hubo varias respuestas, se muestra la menos favorable.</div>
</div>
"""


@app.route("/")
def index():
    return "Sistema de seguimiento AVP activo."


@app.route("/r/<token>")
def ver_actividad(token):
    obj, fuente = obtener_actividad_o_respuesta(token)
    if obj is None:
        return render_template_string(TPL_NO_ENCONTRADO)

    # Se permite volver a abrir el formulario.
    # Si ya existe respuesta, la regla de consolidación se aplica al registrar.
    return render_template_string(TPL_ACTIVIDAD, a=obj, responsable_nombre=mostrar_nombre(obj))


@app.route("/registrar/<token>", methods=["POST"])
def registrar(token):
    estado = request.form.get("estado", "").strip()
    canal = request.form.get("canal", "Link")
    nueva_fecha = request.form.get("nueva_fecha") or ""
    avance = request.form.get("avance") or ""
    status = request.form.get("comentario") or ""

    if estado == "Culminado":
        avance = "100"
        nueva_fecha = ""

    obj, fuente = obtener_actividad_o_respuesta(token)
    if obj is None:
        return render_template_string(TPL_NO_ENCONTRADO)

    row, accion = guardar_respuesta_local(token, estado, canal, nueva_fecha, avance, status)

    if row is None:
        return render_template_string(TPL_NO_ENCONTRADO)

    respuesta = row_to_dict(row) if not isinstance(row, dict) else row

    try:
        guardar_respuesta_github(respuesta)
    except Exception as e:
        print(f"Error guardando respuesta en GitHub: {e}")

    return render_template_string(TPL_REGISTRADO, a=respuesta, responsable_nombre=mostrar_nombre(respuesta))


@app.route("/api/actividad", methods=["POST"])
def api_actividad():
    if not validar_api(request):
        return jsonify({"ok": False, "error": "No autorizado"}), 401

    data = request.get_json(force=True)
    token = data.get("token")
    if not token:
        return jsonify({"ok": False, "error": "Falta token"}), 400

    accion = insertar_o_actualizar_local(data, conservar_si_respondido=True)

    github_ok, github_error = False, ""
    try:
        guardar_actividad_github(data)
        github_ok = True
    except Exception as e:
        github_error = str(e)
        print(f"Error guardando actividad en GitHub: {github_error}")

    return jsonify({
        "ok": True,
        "accion": accion,
        "token": token,
        "github_ok": github_ok,
        "github_error": github_error
    })


@app.route("/api/respuestas", methods=["GET"])
def api_respuestas():
    if not validar_api(request):
        return jsonify({"ok": False, "error": "No autorizado"}), 401

    conn = get_conn()
    rows = conn.execute("""
        SELECT token, proyecto, hoja, grupo, item, id_actividad, responsable, responsable_nombre,
               email, telefono, actividad, fecha_inicio, fecha_programada,
               proximas_acciones, respondido, estado, canal, fecha_respuesta,
               nueva_fecha, avance, comentario
        FROM actividades
        ORDER BY fecha_programada, proyecto, grupo, actividad
    """).fetchall()
    conn.close()

    return jsonify({"ok": True, "total": len(rows), "data": [row_to_dict(r) for r in rows]})


@app.route("/debug/github/<token>")
def debug_github(token):
    if not github_enabled():
        return jsonify({"github_enabled": False, "error": "GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO/GITHUB_BRANCH no configurado"})

    out = {"github_enabled": True, "token": token}

    try:
        act, _ = gh_get_json(path_actividad(token))
        out["actividad_en_github"] = act is not None
    except Exception as e:
        out["actividad_error"] = str(e)

    try:
        resp, _ = gh_get_json(path_respuesta(token))
        out["respuesta_en_github"] = resp is not None
    except Exception as e:
        out["respuesta_error"] = str(e)

    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
