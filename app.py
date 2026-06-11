from flask import Flask, render_template, request, jsonify
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
            comentario TEXT
        )
    """)
    conn.commit()
    conn.close()


init_db()


def validar_api(req):
    clave = req.headers.get("X-API-KEY", "")
    return clave == API_KEY


@app.route("/")
def index():
    return "Sistema de seguimiento AVP activo."


@app.route("/r/<token>")
def ver_actividad(token):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM actividades WHERE token = ?",
        (token,)
    ).fetchone()
    conn.close()

    if row is None:
        return render_template("no_encontrado.html")

    if row["respondido"] == 1:
        return render_template("ya_registrado.html", a=row)

    return render_template("actividad.html", a=row)


@app.route("/registrar/<token>", methods=["POST"])
def registrar(token):
    estado = request.form.get("estado")
    canal = request.form.get("canal", "Link")
    nueva_fecha = request.form.get("nueva_fecha") or ""
    comentario = request.form.get("comentario") or ""
    fecha_respuesta = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM actividades WHERE token = ?",
        (token,)
    ).fetchone()

    if row is None:
        conn.close()
        return render_template("no_encontrado.html")

    if row["respondido"] == 1:
        conn.close()
        return render_template("ya_registrado.html", a=row)

    conn.execute("""
        UPDATE actividades
        SET respondido = 1,
            estado = ?,
            canal = ?,
            fecha_respuesta = ?,
            nueva_fecha = ?,
            comentario = ?
        WHERE token = ?
    """, (estado, canal, fecha_respuesta, nueva_fecha, comentario, token))

    conn.commit()

    row = conn.execute(
        "SELECT * FROM actividades WHERE token = ?",
        (token,)
    ).fetchone()

    conn.close()
    return render_template("registrado.html", a=row)


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
        "email": data.get("email", ""),
        "telefono": data.get("telefono", ""),
        "actividad": data.get("actividad", ""),
        "fecha_inicio": data.get("fecha_inicio", ""),
        "fecha_programada": data.get("fecha_programada", ""),
        "proximas_acciones": data.get("proximas_acciones", ""),
    }

    conn = get_conn()
    existe = conn.execute(
        "SELECT respondido FROM actividades WHERE token = ?",
        (token,)
    ).fetchone()

    if existe is None:
        conn.execute("""
            INSERT INTO actividades (
                token, proyecto, hoja, grupo, item, responsable, email, telefono,
                actividad, fecha_inicio, fecha_programada, proximas_acciones,
                respondido
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            token, campos["proyecto"], campos["hoja"], campos["grupo"], campos["item"],
            campos["responsable"], campos["email"], campos["telefono"], campos["actividad"],
            campos["fecha_inicio"], campos["fecha_programada"], campos["proximas_acciones"]
        ))
        accion = "insertado"
    else:
        # No borra respuesta existente; solo actualiza datos base si todavía no respondió.
        if existe["respondido"] == 0:
            conn.execute("""
                UPDATE actividades
                SET proyecto = ?, hoja = ?, grupo = ?, item = ?, responsable = ?,
                    email = ?, telefono = ?, actividad = ?, fecha_inicio = ?,
                    fecha_programada = ?, proximas_acciones = ?
                WHERE token = ?
            """, (
                campos["proyecto"], campos["hoja"], campos["grupo"], campos["item"],
                campos["responsable"], campos["email"], campos["telefono"], campos["actividad"],
                campos["fecha_inicio"], campos["fecha_programada"], campos["proximas_acciones"],
                token
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
        SELECT token, proyecto, hoja, grupo, item, responsable, email, telefono,
               actividad, fecha_inicio, fecha_programada, proximas_acciones,
               respondido, estado, canal, fecha_respuesta, nueva_fecha, comentario
        FROM actividades
        ORDER BY fecha_programada, proyecto, grupo, actividad
    """).fetchall()
    conn.close()

    data = [dict(r) for r in rows]
    return jsonify({"ok": True, "total": len(data), "data": data})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
