from flask import Flask, render_template_string, request, jsonify
import sqlite3
from datetime import datetime
import os, json, base64, re
import requests

"""
VERSIÓN 1.0 - HISTORIAL POR ID_ACTIVIDAD

- Las actividades continúan almacenándose por token.
- Las respuestas se almacenan en un único JSON por ID_ACTIVIDAD.
- Cada nueva respuesta se agrega a la lista "respuestas".
- El token queda como referencia histórica del formulario.
- Soporta varios responsables y varias reprogramaciones sin sobrescribir el historial.
- La respuesta local conserva el último envío recibido; la consolidación oficial se realiza desde el historial GitHub.
- Valida que el código ?resp= pertenezca realmente a los responsables de la actividad.
"""

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


def normalizar_codigo(valor):
    return str(valor or "").strip().upper()


def separar_codigos_responsables(valor):
    texto = normalizar_codigo(valor)
    if not texto:
        return []
    partes = re.split(r"\+|/|,|;|&|\s+Y\s+", texto)
    salida = []
    vistos = set()
    for parte in partes:
        codigo = normalizar_codigo(parte)
        if codigo and codigo not in vistos:
            salida.append(codigo)
            vistos.add(codigo)
    return salida


def codigo_responsable_valido(actividad, codigo):
    codigo = normalizar_codigo(codigo)
    if not codigo:
        return False
    return codigo in separar_codigos_responsables(getv(actividad, "responsable", ""))


def nombre_individual_responsable(actividad, codigo):
    """
    Intenta emparejar códigos como TATI+BRENDA con los nombres completos
    guardados como 'Tatiana..., Brenda...'.
    """
    codigo = normalizar_codigo(codigo)
    codigos = separar_codigos_responsables(getv(actividad, "responsable", ""))
    nombres_txt = str(getv(actividad, "responsable_nombre", "") or "").strip()
    nombres = [n.strip() for n in re.split(r"\s*[,;]\s*", nombres_txt) if n.strip()]

    if codigo in codigos:
        idx = codigos.index(codigo)
        if idx < len(nombres):
            return nombres[idx]

    return codigo or mostrar_nombre(actividad)


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


def path_respuesta_id(id_actividad):
    """
    Nuevo formato: un único archivo por ID_ACTIVIDAD.
    Ejemplo:
      data/respuestas/AVP-FC13E21E.json
    """
    return f"{GITHUB_RESPUESTAS_DIR}/{id_actividad}.json"


def path_respuesta_token_antigua(token):
    """
    Compatibilidad temporal con el formato anterior:
      data/respuestas/AVP-TOKEN.json
    """
    return f"{GITHUB_RESPUESTAS_DIR}/{token}.json"


def guardar_actividad_github(data):
    token = data.get("token", "")
    if token:
        return gh_put_json(path_actividad(token), data, f"Guardar actividad {token}")
    return {"ok": False, "error": "sin token"}


def crear_historial_base(actividad):
    return {
        "id_actividad": actividad.get("id_actividad", ""),
        "actividad": actividad.get("actividad", ""),
        "proyecto": actividad.get("proyecto", ""),
        "hoja": actividad.get("hoja", ""),
        "grupo": actividad.get("grupo", ""),
        "item": actividad.get("item", ""),
        "responsable": actividad.get("responsable", ""),
        "responsable_nombre": actividad.get("responsable_nombre", ""),
        "fecha_inicio": actividad.get("fecha_inicio", ""),
        "fecha_programada_actual": actividad.get("fecha_programada", ""),
        "ultima_actualizacion": ahora_txt(),
        "respuestas": [],
    }


def entrada_historial_desde_respuesta(respuesta):
    """
    Convierte una respuesta en una entrada inmutable del historial.
    El token se conserva como referencia del aviso/formulario, pero la identidad
    permanente es ID_ACTIVIDAD.
    """
    return {
        "token": respuesta.get("token", ""),
        "responsable_respuesta": respuesta.get("responsable_respuesta", ""),
        "responsable_nombre_respuesta": respuesta.get("responsable_nombre_respuesta", ""),
        "estado": respuesta.get("estado", ""),
        "canal": respuesta.get("canal", "Link"),
        "fecha_respuesta": respuesta.get("fecha_respuesta", ahora_txt()),
        "nueva_fecha": respuesta.get("nueva_fecha", ""),
        "avance": respuesta.get("avance", ""),
        "comentario": respuesta.get("comentario", ""),
    }


def guardar_respuesta_historial_github(actividad, respuesta, reintentos=4):
    """
    Guarda todas las respuestas de una actividad dentro de un solo JSON por ID_ACTIVIDAD.

    La operación se reintenta si GitHub devuelve conflicto 409, lo cual puede ocurrir
    cuando dos responsables responden casi al mismo tiempo.
    """
    id_actividad = str(actividad.get("id_actividad", "") or "").strip()
    if not id_actividad:
        return {"ok": False, "error": "sin id_actividad"}

    path = path_respuesta_id(id_actividad)
    entrada = entrada_historial_desde_respuesta(respuesta)

    ultimo_error = None
    for intento in range(1, reintentos + 1):
        try:
            historial, sha = gh_get_json(path)
            if not isinstance(historial, dict):
                historial = crear_historial_base(actividad)

            respuestas = historial.get("respuestas")
            if not isinstance(respuestas, list):
                respuestas = []

            # Evita duplicar exactamente el mismo envío por doble clic/recarga.
            firma_nueva = (
                str(entrada.get("token", "")),
                str(entrada.get("responsable_respuesta", "")),
                str(entrada.get("fecha_respuesta", "")),
                str(entrada.get("estado", "")),
                str(entrada.get("avance", "")),
                str(entrada.get("comentario", "")),
            )
            firmas = {
                (
                    str(x.get("token", "")),
                    str(x.get("responsable_respuesta", "")),
                    str(x.get("fecha_respuesta", "")),
                    str(x.get("estado", "")),
                    str(x.get("avance", "")),
                    str(x.get("comentario", "")),
                )
                for x in respuestas if isinstance(x, dict)
            }
            if firma_nueva not in firmas:
                respuestas.append(entrada)

            historial.update({
                "id_actividad": id_actividad,
                "actividad": actividad.get("actividad", historial.get("actividad", "")),
                "proyecto": actividad.get("proyecto", historial.get("proyecto", "")),
                "hoja": actividad.get("hoja", historial.get("hoja", "")),
                "grupo": actividad.get("grupo", historial.get("grupo", "")),
                "item": actividad.get("item", historial.get("item", "")),
                "responsable": actividad.get("responsable", historial.get("responsable", "")),
                "responsable_nombre": actividad.get("responsable_nombre", historial.get("responsable_nombre", "")),
                "fecha_inicio": actividad.get("fecha_inicio", historial.get("fecha_inicio", "")),
                "fecha_programada_actual": actividad.get("fecha_programada", historial.get("fecha_programada_actual", "")),
                "ultima_actualizacion": ahora_txt(),
                "respuestas": respuestas,
            })

            content = json.dumps(historial, ensure_ascii=False, indent=2)
            payload = {
                "message": f"Registrar respuesta de {id_actividad}",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": GITHUB_BRANCH,
            }
            if sha:
                payload["sha"] = sha

            r = requests.put(
                gh_url(path),
                headers=gh_headers(),
                data=json.dumps(payload),
                timeout=30,
            )

            if r.status_code == 409 and intento < reintentos:
                ultimo_error = f"Conflicto GitHub 409, intento {intento}"
                time.sleep(1.5 * intento)
                continue

            r.raise_for_status()
            return {"ok": True, "path": path, "total_respuestas": len(respuestas)}

        except Exception as e:
            ultimo_error = str(e)
            if intento < reintentos:
                time.sleep(1.5 * intento)
                continue
            raise

    return {"ok": False, "error": ultimo_error or "No se pudo guardar historial"}


def recuperar_actividad_github(token):
    data, _sha = gh_get_json(path_actividad(token))
    return data


def recuperar_historial_por_id(id_actividad):
    if not id_actividad:
        return None
    data, _sha = gh_get_json(path_respuesta_id(id_actividad))
    return data


def recuperar_respuesta_github_antigua(token):
    data, _sha = gh_get_json(path_respuesta_token_antigua(token))
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
    Guarda en SQLite la última respuesta recibida para ese token.

    La regla de negocio definitiva ya no se consolida dentro de SQLite:
    el historial completo por ID_ACTIVIDAD se guarda en GitHub y los scripts
    V1 eligen la última respuesta de cada responsable.
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM actividades WHERE token = ?", (token,)).fetchone()

    if row is None:
        conn.close()
        return None, "no_encontrado"

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
    """, (
        estado_nuevo,
        canal,
        ahora_txt(),
        nueva_fecha,
        avance_nuevo,
        status,
        token
    ))

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

    actividad = recuperar_actividad_github(token)
    if actividad:
        insertar_o_actualizar_local(actividad, conservar_si_respondido=False)
        row = obtener_local(token)
        return row, "github_actividad"

    # Compatibilidad con respuestas antiguas guardadas por token.
    respuesta_antigua = recuperar_respuesta_github_antigua(token)
    if respuesta_antigua:
        return respuesta_antigua, "github_respuesta_antigua"

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
        <input type="hidden" name="responsable_respuesta" value="{{ responsable_respuesta }}">
        <input type="hidden" name="responsable_nombre_respuesta" value="{{ responsable_nombre_respuesta }}">
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
    <div class="nota">Cada respuesta se guarda en el historial de la actividad y se consolida posteriormente en el cronograma.</div>
</div>
"""

TPL_REGISTRADO = CSS + """
<div class="card">
    <h1 class="ok">Respuesta registrada</h1>
    <div class="sub">Proyecto Anillo Vial Periférico</div>
    <table class="tabla">
        <tr><td class="label">Responsable</td><td>{{ responsable_nombre }}</td></tr>
        <tr><td class="label">Actividad</td><td class="actividad">{{ a['actividad'] }}</td></tr>
        <tr><td class="label">Estado registrado</td><td>{{ a['estado'] }}</td></tr>
        {% if a['avance'] %}<tr><td class="label">Avance</td><td>{{ a['avance'] }}%</td></tr>{% endif %}
        {% if a['nueva_fecha'] %}<tr><td class="label">Nueva fecha</td><td>{{ a['nueva_fecha'] }}</td></tr>{% endif %}
        {% if a['comentario'] %}<tr><td class="label">Status</td><td>{{ a['comentario'] }}</td></tr>{% endif %}
        <tr><td class="label">Fecha de respuesta</td><td>{{ a['fecha_respuesta'] }}</td></tr>
    </table>
    <div class="nota">La respuesta fue añadida al historial permanente de la actividad.</div>
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

    codigo_resp = normalizar_codigo(request.args.get("resp", ""))

    # El código del enlace debe pertenecer a la actividad.
    if codigo_resp and not codigo_responsable_valido(obj, codigo_resp):
        return render_template_string(TPL_NO_ENCONTRADO)

    # Compatibilidad con enlaces antiguos de actividades con un solo responsable.
    if not codigo_resp:
        codigos = separar_codigos_responsables(getv(obj, "responsable", ""))
        if len(codigos) == 1:
            codigo_resp = codigos[0]

    nombre_resp = nombre_individual_responsable(obj, codigo_resp)

    return render_template_string(
        TPL_ACTIVIDAD,
        a=obj,
        responsable_nombre=nombre_resp,
        responsable_respuesta=codigo_resp,
        responsable_nombre_respuesta=nombre_resp,
    )


@app.route("/registrar/<token>", methods=["POST"])
def registrar(token):
    estado = request.form.get("estado", "").strip()
    canal = request.form.get("canal", "Link")
    nueva_fecha = request.form.get("nueva_fecha") or ""
    avance = request.form.get("avance") or ""
    status = request.form.get("comentario") or ""
    responsable_respuesta = str(request.form.get("responsable_respuesta", "") or "").strip().upper()
    responsable_nombre_respuesta = str(request.form.get("responsable_nombre_respuesta", "") or "").strip()

    if estado == "Culminado":
        avance = "100"
        nueva_fecha = ""

    obj, fuente = obtener_actividad_o_respuesta(token)
    if obj is None:
        return render_template_string(TPL_NO_ENCONTRADO)

    actividad = row_to_dict(obj) if not isinstance(obj, dict) else dict(obj)

    # Respaldo para enlaces antiguos con un solo responsable.
    if not responsable_respuesta:
        codigos = separar_codigos_responsables(actividad.get("responsable", ""))
        if len(codigos) == 1:
            responsable_respuesta = codigos[0]

    # No aceptar un código ajeno a los responsables reales de la actividad.
    if not responsable_respuesta or not codigo_responsable_valido(actividad, responsable_respuesta):
        return render_template_string(TPL_NO_ENCONTRADO)

    responsable_nombre_respuesta = nombre_individual_responsable(
        actividad, responsable_respuesta
    )

    fecha_respuesta = ahora_txt()

    respuesta_historial = {
        "token": token,
        "id_actividad": actividad.get("id_actividad", ""),
        "responsable_respuesta": responsable_respuesta,
        "responsable_nombre_respuesta": responsable_nombre_respuesta,
        "estado": estado,
        "canal": canal,
        "fecha_respuesta": fecha_respuesta,
        "nueva_fecha": nueva_fecha,
        "avance": avance,
        "comentario": status,
    }

    # Mantiene una copia local para continuidad operativa de Render,
    # pero GitHub conserva el historial completo y es la fuente auditable.
    row, accion = guardar_respuesta_local(
        token, estado, canal, nueva_fecha, avance, status
    )

    if row is None:
        return render_template_string(TPL_NO_ENCONTRADO)

    try:
        resultado_git = guardar_respuesta_historial_github(
            actividad, respuesta_historial
        )
        print(
            f"Historial GitHub actualizado: {actividad.get('id_actividad','')} | "
            f"{responsable_respuesta} | {resultado_git}"
        )
    except Exception as e:
        print(f"Error guardando historial de respuesta en GitHub: {e}")

    respuesta_mostrar = dict(actividad)
    respuesta_mostrar.update({
        "estado": estado,
        "avance": avance,
        "nueva_fecha": nueva_fecha,
        "comentario": status,
        "fecha_respuesta": fecha_respuesta,
        "responsable_respuesta": responsable_respuesta,
    })

    return render_template_string(
        TPL_REGISTRADO,
        a=respuesta_mostrar,
        responsable_nombre=responsable_nombre_respuesta or responsable_respuesta or mostrar_nombre(actividad),
    )


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
        return jsonify({
            "github_enabled": False,
            "error": "GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO/GITHUB_BRANCH no configurado"
        })

    out = {"github_enabled": True, "token": token}

    actividad = None
    try:
        actividad, _ = gh_get_json(path_actividad(token))
        out["actividad_en_github"] = actividad is not None
    except Exception as e:
        out["actividad_error"] = str(e)

    id_actividad = ""
    if isinstance(actividad, dict):
        id_actividad = str(actividad.get("id_actividad", "") or "").strip()
    out["id_actividad"] = id_actividad

    try:
        historial = recuperar_historial_por_id(id_actividad) if id_actividad else None
        out["respuesta_en_github"] = bool(
            isinstance(historial, dict) and historial.get("respuestas")
        )
        out["historial_en_github"] = historial is not None
        out["total_respuestas"] = (
            len(historial.get("respuestas", []))
            if isinstance(historial, dict) else 0
        )
    except Exception as e:
        out["respuesta_error"] = str(e)

    # Solo diagnóstico de compatibilidad con archivos antiguos.
    try:
        antigua, _ = gh_get_json(path_respuesta_token_antigua(token))
        out["respuesta_antigua_por_token"] = antigua is not None
    except Exception as e:
        out["respuesta_antigua_error"] = str(e)

    return jsonify(out)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
