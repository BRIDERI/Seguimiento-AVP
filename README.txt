Sistema web minimo para seguimiento AVP.

1. Instalar local:
   pip install -r requirements.txt

2. Ejecutar local:
   python app.py

3. Abrir:
   http://127.0.0.1:5000

4. Para publicar en Render:
   - Subir estos archivos a GitHub.
   - Crear Web Service en Render.
   - Build: pip install -r requirements.txt
   - Start: gunicorn app:app
