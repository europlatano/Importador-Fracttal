import io
import hmac

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Importador Fracttal | Prueba API", page_icon="🛠️", layout="wide")
st.title("🛠️ Importador de planes Fracttal")
st.caption("Europlátano · Versión 2 · Comprobación de conexión en modo SOLO LECTURA")
st.warning("Esta versión NO crea, edita ni elimina registros. Para importar realmente necesitaremos validar primero el formato de los campos con Fracttal.")

# Streamlit Secrets stay in the server configuration, never in GitHub or Excel.
def setting(name):
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""

app_password = setting("APP_PASSWORD")
if not app_password:
    st.error("Acceso protegido sin configurar. El administrador debe definir APP_PASSWORD en los secretos de Streamlit antes de usar esta versión.")
    st.stop()

if not st.session_state.get("authenticated", False):
    with st.form("login"):
        supplied = st.text_input("Contraseña de acceso al importador", type="password")
        submitted = st.form_submit_button("Entrar")
    if submitted:
        if hmac.compare_digest(supplied, app_password):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta")
    st.stop()

with st.sidebar:
    st.success("Sesión protegida")
    if st.button("Cerrar sesión"):
        st.session_state.authenticated = False
        st.rerun()
    st.caption("No escribas claves OAuth en esta página ni las subas a GitHub.")

st.header("1 · Comprobar conexión con Fracttal")
client_key = setting("FRACTTAL_CLIENT_ID")
client_secret = setting("FRACTTAL_CLIENT_SECRET")
ready = bool(client_key and client_secret)
st.write("Credenciales OAuth configuradas en el servidor:", "✅ Sí" if ready else "⚠️ No todavía")

# No POSTs that create data. The only POST obtains the ephemeral OAuth token.
def check_connection():
    try:
        token_response = requests.post(
            "https://one.fracttal.com/oauth/token",
            auth=(client_key, client_secret),
            data={"grant_type": "client_credentials"},
            timeout=20,
        )
        if token_response.status_code != 200:
            return None, f"Error de autenticación OAuth (HTTP {token_response.status_code}). Comprueba credenciales, cliente y grupo de permisos."
        access_token = token_response.json().get("access_token")
        if not access_token:
            return None, "Fracttal no devolvió un token de acceso válido."
        plans_response = requests.get(
            "https://app.fracttal.com/api/groups_tasks/",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params={"start": 0, "limit": 100},
            timeout=25,
        )
        if plans_response.status_code != 200:
            return None, f"Autenticación realizada, pero la consulta de planes no está autorizada o ha fallado (HTTP {plans_response.status_code}). Comprueba el permiso CONSULTAR de planes para el grupo addon."
        response = plans_response.json()
        if response.get("success") is False:
            return None, "La API respondió, pero no confirmó una consulta correcta."
        if not isinstance(response.get("data"), list):
            return None, "La respuesta no tiene el formato esperado para una lista de planes."
        return {"plans": response["data"], "total": response.get("total")}, None
    except requests.Timeout:
        return None, "Tiempo de espera agotado al contactar con Fracttal."
    except (requests.RequestException, ValueError):
        return None, "Error de conexión o respuesta JSON no válida."

if st.button("🔗 Probar conexión (solo consultar planes)", disabled=not ready, type="primary"):
    with st.spinner("Consultando Fracttal sin modificar registros..."):
        result, error = check_connection()
    if error:
        st.error(error)
    else:
        st.success("✅ OAuth correcto y consulta de planes autorizada. No se ha modificado ningún dato.")
        st.caption("Se ha comprobado la primera página de hasta 100 planes; el total puede incluir más registros.")
        st.metric("Planes recibidos en primera página", len(result["plans"]))
        # Deliberately avoid displaying company-sensitive plan names on a demo hosted externally.

st.divider()
st.header("2 · Comprobar plantilla Excel")
uploaded = st.file_uploader("Selecciona la plantilla Excel (.xlsx)", type=["xlsx"])
if uploaded is None:
    st.info("Carga el Excel de la Reisopack 2904 para validar su estructura y visualizar el contenido.")
    st.stop()

try:
    workbook = pd.read_excel(io.BytesIO(uploaded.getvalue()), sheet_name=None, dtype=str, keep_default_na=False)
except Exception:
    st.error("No se pudo leer el archivo. Comprueba que sea un Excel .xlsx válido.")
    st.stop()

required = {
    "PLANES": ["ID_PLAN", "NOMBRE_PLAN"],
    "TAREAS": ["ID_TAREA", "ID_PLAN", "NOMBRE_TAREA", "ACTIVADOR"],
    "SUBTAREAS": ["ID_TAREA", "ORDEN", "TIPO_SUBTAREA", "DESCRIPCION"],
}
errors = []
for sheet, columns in required.items():
    if sheet not in workbook:
        errors.append(f"Falta la hoja {sheet}.")
    else:
        for column in columns:
            if column not in workbook[sheet].columns:
                errors.append(f"Falta la columna {column} en {sheet}.")
if errors:
    for error in errors:
        st.error(error)
    st.stop()

plans, tasks, subtasks = (workbook[k].copy() for k in ("PLANES", "TAREAS", "SUBTAREAS"))
for col in ("ID_PLAN", "NOMBRE_PLAN"):
    if plans[col].str.strip().eq("").any():
        errors.append(f"PLANES: hay valores vacíos en {col}.")
for col in ("ID_TAREA", "ID_PLAN", "NOMBRE_TAREA", "ACTIVADOR"):
    if tasks[col].str.strip().eq("").any():
        errors.append(f"TAREAS: hay valores vacíos en {col}.")
if plans.ID_PLAN.duplicated().any():
    errors.append("Hay ID_PLAN duplicados.")
if tasks.ID_TAREA.duplicated().any():
    errors.append("Hay ID_TAREA duplicados.")
unknown_plan = set(tasks.ID_PLAN) - set(plans.ID_PLAN)
if unknown_plan:
    errors.append("Tareas vinculadas a planes inexistentes: " + ", ".join(sorted(unknown_plan)))
unknown_task = set(subtasks.ID_TAREA) - set(tasks.ID_TAREA)
if unknown_task:
    errors.append("Subtareas vinculadas a tareas inexistentes: " + ", ".join(sorted(unknown_task)))
if not subtasks.empty:
    if subtasks[["ID_TAREA", "ORDEN"]].duplicated().any():
        errors.append("Hay órdenes de subtarea duplicados dentro de una tarea.")
    if subtasks.DESCRIPCION.str.strip().eq("").any():
        errors.append("Hay subtareas sin descripción.")
for val in tasks.ACTIVADOR.unique():
    if val not in ("Fecha", "Evento"):
        errors.append(f"Activador no reconocido: {val}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Planes", len(plans))
c2.metric("Tareas", len(tasks))
c3.metric("Subtareas", len(subtasks))
c4.metric("Incidencias", len(errors))
if errors:
    st.error("Validación con incidencias: " + "; ".join(errors))
else:
    st.success("Validación estructural correcta. Pendiente de validar la correspondencia con los catálogos y endpoints de Fracttal.")

st.subheader("Vista previa")
for name, frame in (("Planes", plans), ("Tareas", tasks), ("Subtareas", subtasks)):
    with st.expander(f"{name} ({len(frame)})", expanded=(name == "Tareas")):
        st.dataframe(frame, use_container_width=True, hide_index=True)

st.subheader("Detalle por tarea")
if not tasks.empty:
    options = [f"{row.ID_TAREA} · {row.NOMBRE_TAREA}" for row in tasks.itertuples()]
    chosen = st.selectbox("Selecciona una tarea", options)
    task_id = chosen.split(" · ")[0]
    st.dataframe(subtasks[subtasks.ID_TAREA == task_id], use_container_width=True, hide_index=True)

st.divider()
st.button("Importar a Fracttal (no habilitado en la versión de conexión)", disabled=True, use_container_width=True)
st.caption("Después de comprobar la conexión, prepararemos el importador con confirmación explícita, revisión de IDs de catálogos y control de duplicados.")
