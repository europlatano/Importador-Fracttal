"""Europlatano Fracttal test importer V3: deliberate, auditable test writes only."""
import io
import hmac
import requests
import pandas as pd
import streamlit as st

API = 'https://app.fracttal.com/api'
TOKEN_URL = 'https://one.fracttal.com/oauth/token'
TEST_PLAN = 'PRUEBA API – IMPORTADOR EUROPLÁTANO'
TEST_TASK = 'PRUEBA API – INSPECCIÓN DE EJEMPLO (NO OPERATIVA)'
TEST_EVENT = 'PRUEBA API – ACTIVACIÓN MANUAL (NO OPERATIVA)'

st.set_page_config(page_title='Importador Fracttal · V3', page_icon='🛠️', layout='wide')
st.title('🛠️ Importador de planes Fracttal')
st.caption('Europlátano · Versión 3 · Prueba de escritura controlada SIN ASOCIAR ACTIVOS')
st.warning('Esta versión permite crear SOLO un plan de PRUEBA y, por separado, una tarea de PRUEBA. La importación masiva del Excel aún NO está habilitada.')


def setting(key):
    try:
        value = st.secrets.get(key, '')
        return str(value).strip() if value is not None else ''
    except Exception:
        return ''

password = setting('APP_PASSWORD')
if not password:
    st.error('Configura APP_PASSWORD en Settings → Secrets de Streamlit.')
    st.stop()
if not st.session_state.get('authenticated', False):
    with st.form('login'):
        entered = st.text_input('Contraseña del importador', type='password')
        submitted = st.form_submit_button('Entrar')
    if submitted:
        if hmac.compare_digest(entered, password):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error('Contraseña incorrecta.')
    st.stop()
with st.sidebar:
    st.success('Sesión protegida')
    if st.button('Cerrar sesión'):
        st.session_state.authenticated = False
        st.rerun()
    st.caption('No guardes secretos en GitHub ni los muestres en capturas.')

client_id = setting('FRACTTAL_CLIENT_ID')
client_secret = setting('FRACTTAL_CLIENT_SECRET')
ready = bool(client_id and client_secret)


def get_token():
    response = requests.post(TOKEN_URL, auth=(client_id, client_secret),
                             data={'grant_type': 'client_credentials'}, timeout=22)
    if response.status_code != 200:
        raise ValueError(f'OAuth rechazado (HTTP {response.status_code}).')
    token = response.json().get('access_token')
    if not token:
        raise ValueError('OAuth no devolvió access_token.')
    return token


def call_api(method, endpoint, token, payload=None, params=None):
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    response = requests.request(method, API + endpoint, headers=headers,
                                json=payload, params=params, timeout=28)
    try:
        parsed = response.json()
    except ValueError:
        parsed = {}
    if response.status_code >= 400 or parsed.get('success') is False:
        # Prevent accidental display of raw server errors that could contain sensitive data.
        raise ValueError(f'{method} {endpoint} rechazado (HTTP {response.status_code}). Consulta los permisos y catálogos.')
    return response.status_code, parsed


def fetch_all_plans(token):
    """Scan every reported page, fail closed if pagination cannot be verified."""
    found = []
    for offset in range(0, 10000, 100):
        _, response = call_api('GET', '/groups_tasks/', token,
                               params={'start': offset, 'limit': 100})
        rows = response.get('data')
        if not isinstance(rows, list):
            raise ValueError('La API no devolvió una lista de planes.')
        found.extend(rows)
        total = response.get('total')
        try:
            total_int = int(total) if total is not None else None
        except (TypeError, ValueError):
            total_int = None
        if (total_int is not None and len(found) >= total_int) or len(rows) < 100:
            return found
    raise ValueError('No se completó la revisión de todos los planes. Se bloquea la creación.')


def matching_test_plans(rows):
    return [p for p in rows if str(p.get('description', '')).strip().casefold() == TEST_PLAN.casefold()]


def safe_api_action(fn):
    try:
        return fn(), None
    except requests.Timeout:
        return None, 'La API tardó demasiado. NO repitas la creación sin consultar antes si ya se creó el registro.'
    except requests.RequestException:
        return None, 'Fallo de red. NO repitas la creación hasta consultar Fracttal.'
    except (ValueError, TypeError, KeyError) as exc:
        return None, str(exc)

st.header('1 · Conexión y revisión de duplicados')
st.write('Credenciales guardadas en Streamlit:', '✅ Configuradas' if ready else '❌ Pendientes')

if st.button('🔗 Consultar planes existentes (solo lectura)', disabled=not ready):
    def load_plans():
        token = get_token()
        return fetch_all_plans(token)
    plans, error = safe_api_action(load_plans)
    if error:
        st.error(error)
        st.session_state.pop('verified_plans', None)
    else:
        st.session_state.verified_plans = plans
        st.success('Consulta correcta. Ningún registro modificado.')
        st.metric('Planes consultados', len(plans))
        duplicates = matching_test_plans(plans)
        st.info(f'Planes con el nombre de prueba: {len(duplicates)}')

st.divider()
st.header('2 · Crear un plan de prueba (sin activos)')
st.code(TEST_PLAN, language=None)
st.caption('Se creará un plan nuevo sin localización ni activos. No se crean órdenes de trabajo ni se toca ningún equipo.')
st.info('Solo para probar los permisos de creación. No se importará todavía el plan Reisopack.')
confirm_plan = st.checkbox('Entiendo que esta acción CREARÁ un registro real de prueba en Fracttal.', key='confirm_plan')
typed_plan = st.text_input('Para autorizarlo, escribe exactamente CREAR PLAN PRUEBA', key='typed_plan')
can_create_plan = ready and confirm_plan and typed_plan == 'CREAR PLAN PRUEBA'

if st.button('🧪 Crear plan de prueba en Fracttal', disabled=not can_create_plan, type='primary'):
    def create_test_plan():
        token = get_token()
        existing = matching_test_plans(fetch_all_plans(token))
        if existing:
            return 'EXISTE', existing[0].get('id')
        status, result = call_api('POST', '/groups_tasks/', token, payload={'description': TEST_PLAN})
        if status != 201 or not result.get('data', {}).get('id'):
            raise ValueError('Respuesta de creación sin confirmación suficiente. Comprueba Fracttal antes de repetir.')
        return 'CREADO', result['data']['id']
    outcome, error = safe_api_action(create_test_plan)
    if error:
        st.error(error)
    else:
        state, plan_id = outcome
        st.session_state.test_plan_id = int(plan_id)
        if state == 'CREADO':
            st.success(f'Plan de prueba creado correctamente. ID: {plan_id}. Sin activos vinculados.')
        else:
            st.warning(f'El plan de prueba ya existía (ID: {plan_id}); no se ha duplicado.')

st.divider()
st.header('3 · Crear una tarea mínima de prueba (opcional)')
st.write('La creación de tareas requiere un **ID numérico del tipo principal de tarea** existente en vuestro catálogo.')
st.warning('**No adivinaremos el ID.** Si no conoces el ID de Preventivo, deja esta sección pendiente. En una próxima revisión podremos resolverlo con el catálogo.')
st.caption('Si se realiza la prueba, se creará una tarea con activador por evento. Fracttal puede crear también una entrada en el catálogo de eventos. No se asignarán activos.')

def find_id_plan():
    plans, error = safe_api_action(lambda: fetch_all_plans(get_token()))
    if error:
        return None, error
    matches = matching_test_plans(plans)
    if len(matches) != 1:
        return None, f'Se esperaban exactamente 1 plan de prueba; encontrados: {len(matches)}.'
    return int(matches[0]['id']), None

if st.button('🔎 Localizar plan de prueba para añadir una tarea', disabled=not ready):
    plan_id, error = find_id_plan()
    if error:
        st.error(error)
    else:
        st.session_state.test_plan_id = plan_id
        st.success(f'Plan de prueba encontrado. ID: {plan_id}')

plan_id = st.session_state.get('test_plan_id')
st.write('Plan de prueba identificado:', str(plan_id) if plan_id else 'Pendiente')
task_main_type = st.number_input('ID del tipo principal de tarea (confirmado en Fracttal)', min_value=0, step=1, value=0)
ack_event = st.checkbox('Entiendo que se creará una tarea y posiblemente un evento en el catálogo.', key='ack_event')
typed_task = st.text_input('Para autorizar, escribe CREAR TAREA PRUEBA', key='typed_task')
if st.button('🧪 Crear una sola tarea de prueba',
             disabled=not (ready and plan_id and task_main_type > 0 and ack_event and typed_task == 'CREAR TAREA PRUEBA')):
    # Repeated POST may duplicate tasks: one attempt only in this session, never auto-retry.
    if st.session_state.get('task_post_attempted'):
        st.error('Ya se intentó crear la tarea en esta sesión. Verifica Fracttal antes de cualquier nuevo intento.')
    else:
        st.session_state.task_post_attempted = True
        payload = {
            'description': TEST_TASK,
            'id_task_type_main': int(task_main_type),
            'id_group_task': int(plan_id),
            'triggers': [{'id_task_trigger_type': 4, 'event_description': TEST_EVENT}],
            'subtasks': [
                {'description': 'Comprobar visualmente el estado general (solo prueba API)', 'id_task_form_item_type': 4, 'is_required': True},
                {'description': 'Observaciones de prueba (sin datos confidenciales)', 'id_task_form_item_type': 1, 'is_required': False},
            ],
            'resources': [],
        }
        def create_task():
            token = get_token()
            status, result = call_api('POST', '/tasks_plan/', token, payload=payload)
            if status != 201 or not result.get('data', {}).get('id'):
                raise ValueError('La respuesta no confirmó el ID de la tarea. Comprueba Fracttal; no reintentes a ciegas.')
            return result['data']
        result, error = safe_api_action(create_task)
        if error:
            st.error(error)
            st.info('Si recibes HTTP 400, probablemente falte validar el ID de tipo o el evento. Si es 403, revisa el permiso ADD en Plan de tareas del grupo addon.')
        else:
            st.success(f'Tarea de prueba creada. ID: {result["id"]}. Confirma en Fracttal el activador y las 2 subtareas.')

st.divider()
st.header('4 · Validar Excel Reisopack (SIN IMPORTAR)')
uploaded = st.file_uploader('Selecciona la plantilla Excel (.xlsx)', type=['xlsx'])
if uploaded is None:
    st.info('Carga el Excel para revisar su estructura. Los activos se asociarán MANUALMENTE después de importar los planes.')
    st.stop()
try:
    workbook = pd.read_excel(io.BytesIO(uploaded.getvalue()), sheet_name=None, dtype=str, keep_default_na=False)
except Exception:
    st.error('No se pudo leer el archivo .xlsx.')
    st.stop()
required = {
    'PLANES': ['ID_PLAN', 'NOMBRE_PLAN'],
    'TAREAS': ['ID_TAREA', 'ID_PLAN', 'NOMBRE_TAREA', 'ACTIVADOR'],
    'SUBTAREAS': ['ID_TAREA', 'ORDEN', 'TIPO_SUBTAREA', 'DESCRIPCION'],
}
errors = []
for sheet, columns in required.items():
    if sheet not in workbook:
        errors.append(f'Falta la hoja {sheet}.')
    else:
        errors.extend(f'Falta columna {col} en hoja {sheet}.' for col in columns if col not in workbook[sheet].columns)
if errors:
    st.error(' '.join(errors))
    st.stop()
plans, tasks, subtasks = (workbook[k].copy() for k in ('PLANES', 'TAREAS', 'SUBTAREAS'))
if plans.ID_PLAN.duplicated().any() or tasks.ID_TAREA.duplicated().any():
    errors.append('IDs de plan o tarea duplicados.')
if set(tasks.ID_PLAN) - set(plans.ID_PLAN):
    errors.append('Tareas con plan inexistente.')
if set(subtasks.ID_TAREA) - set(tasks.ID_TAREA):
    errors.append('Subtareas con tarea inexistente.')
if not subtasks.empty and subtasks[['ID_TAREA', 'ORDEN']].duplicated().any():
    errors.append('Órdenes de subtarea duplicados.')
if set(tasks.ACTIVADOR) - {'Fecha', 'Evento'}:
    errors.append('Activadores no reconocidos.')
for label, count in [('Planes', len(plans)), ('Tareas', len(tasks)), ('Subtareas', len(subtasks)), ('Incidencias', len(errors))]:
    st.metric(label, count)
if errors:
    st.error('; '.join(errors))
else:
    st.success('Validación estructural del Excel correcta. No confirma aún equivalencia de catálogos API.')
for label, table in [('Planes', plans), ('Tareas', tasks), ('Subtareas', subtasks)]:
    with st.expander(f'{label} ({len(table)})', expanded=label == 'Tareas'):
        st.dataframe(table, use_container_width=True, hide_index=True)
st.button('Importar el Excel completo (pendiente de implementar)', disabled=True)
st.caption('No se asocian activos desde este importador. La asociación se hará a mano en Fracttal.')
