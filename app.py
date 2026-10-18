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

st.set_page_config(page_title='Importador Fracttal · V4', page_icon='🛠️', layout='wide')
st.title('🛠️ Importador de planes Fracttal')
st.caption('Europlátano · Versión 4 · Detección del tipo Preventivo y prueba controlada SIN ACTIVOS')
st.warning('Esta versión permite buscar el ID de Preventivo a partir de tareas existentes y crear, tras confirmación, SOLO una tarea de PRUEBA. No importa el Excel completo.')


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
    if response.status_code >= 400 or (isinstance(parsed, dict) and parsed.get('success') is False):
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
st.header('3 · Localizar tipo Preventivo y probar una tarea')
st.info('Primero buscamos el identificador de Preventivo leyendo tareas YA EXISTENTES. No alteramos catálogos ni adivinamos IDs. Solo crearemos algo tras confirmación expresa.')


def fetch_tasks(token, group_id=None):
    """Paginate full listing; filter by plan on server for duplicate check."""
    found = []
    for offset in range(0, 30000, 100):
        params = {'start': offset, 'limit': 100}
        if group_id is not None:
            params['id_group_task'] = int(group_id)
        _, response = call_api('GET', '/tasks/', token, params=params)
        rows = response.get('data') if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise ValueError('La API no devolvió una lista de tareas.')
        found.extend(rows)
        try:
            total = int(response.get('total')) if response.get('total') is not None else None
        except (TypeError, ValueError):
            total = None
        if (total is not None and len(found) >= total) or len(rows) < 100:
            return found
    raise ValueError('No se pudo recorrer todo el catálogo de tareas. Se bloquea la creación.')


def preventive_type_ids(tasks):
    """Infer existing principal type IDs from Fracttal task records, not guessed API paths."""
    seen = {}
    for row in tasks:
        label = str(row.get('tasks_types_main_description') or '').strip().casefold()
        raw_id = row.get('id_task_type_main')
        if 'preventiv' not in label or not raw_id:
            continue
        try:
            value = int(raw_id)
        except (ValueError, TypeError):
            continue
        if value > 0:
            seen[value] = seen.get(value, 0) + 1
    return seen


def find_id_plan():
    plans, error = safe_api_action(lambda: fetch_all_plans(get_token()))
    if error:
        return None, error
    matches = matching_test_plans(plans)
    if len(matches) != 1:
        return None, f'Se esperaban exactamente 1 plan de prueba; encontrados: {len(matches)}.'
    return int(matches[0]['id']), None

left, right = st.columns(2)
with left:
    if st.button('🔎 Localizar plan de prueba', disabled=not ready):
        plan_id_found, error = find_id_plan()
        if error:
            st.error(error)
            st.session_state.pop('test_plan_id', None)
        else:
            st.session_state.test_plan_id = plan_id_found
            st.success(f'Plan de prueba localizado. ID: {plan_id_found}')
with right:
    if st.button('🔎 Buscar ID de Preventivo automáticamente', disabled=not ready):
        found_tasks, error = safe_api_action(lambda: fetch_tasks(get_token()))
        if error:
            st.error(error)
            st.session_state.pop('preventive_type_id', None)
        else:
            seen = preventive_type_ids(found_tasks)
            st.session_state.pop('preventive_type_id', None)
            st.session_state['preventive_candidates'] = seen
            st.session_state['tasks_scanned'] = len(found_tasks)
            if len(seen) == 1:
                st.session_state.preventive_type_id = next(iter(seen))
                st.success(f'Preventivo localizado: ID {st.session_state.preventive_type_id}. Identificado en {len(found_tasks)} tareas existentes.')
            elif not seen:
                st.warning('No se encontró ningún registro cuya clasificación principal contenga «Preventivo». No se utilizará un ID supuesto.')
            else:
                st.error('Se encontraron varios IDs asociados a Preventivo. Se bloquea la creación hasta revisar esta discrepancia.')

st.write('Plan de prueba:', st.session_state.get('test_plan_id', 'Pendiente'))
st.write('Tipo principal Preventivo:', st.session_state.get('preventive_type_id', 'Pendiente'))
if st.session_state.get('preventive_candidates'):
    st.caption('IDs observados en tareas existentes (ID: número de registros): ' + ', '.join(f'{k}: {v}' for k,v in sorted(st.session_state.preventive_candidates.items())))

st.caption('La prueba crea una sola tarea de EVENTO dentro del plan de prueba. Puede incorporar una entrada en el catálogo de eventos. No vincula activos, no importa planes reales y no crea órdenes de trabajo.')
ack_event = st.checkbox('Autorizo crear UNA tarea de prueba real en el plan de prueba, sin activos.', key='ack_event')
typed_task = st.text_input('Para autorizar, escribe exactamente CREAR TAREA PRUEBA', key='typed_task')
can_create_task = bool(ready and st.session_state.get('test_plan_id') and st.session_state.get('preventive_type_id') and ack_event and typed_task == 'CREAR TAREA PRUEBA')
if st.button('🧪 Crear una sola tarea de prueba', disabled=not can_create_task):
    if st.session_state.get('task_post_attempted'):
        st.error('Ya se intentó crear en esta sesión. Verifica Fracttal y evita repetir la operación.')
    else:
        # Set before any network call to avoid duplicate writes on rerun/timeout.
        st.session_state.task_post_attempted = True
        plan_id = int(st.session_state.test_plan_id)
        main_type_id = int(st.session_state.preventive_type_id)
        payload = {
            'description': TEST_TASK,
            'id_task_type_main': main_type_id,
            'id_group_task': plan_id,
            'triggers': [{'id_task_trigger_type': 4, 'event_description': TEST_EVENT}],
            'subtasks': [
                {'description': 'Comprobar visualmente el estado general (solo prueba API)', 'id_task_form_item_type': 4, 'is_required': True},
                {'description': 'Observaciones de prueba (sin datos confidenciales)', 'id_task_form_item_type': 1, 'is_required': False},
            ],
            'resources': [],
        }
        def attempt_task_once():
            token = get_token()
            prior = [row for row in fetch_tasks(token, group_id=plan_id)
                     if str(row.get('description', '')).strip().casefold() == TEST_TASK.casefold()]
            if prior:
                return 'EXISTE', None
            status, response = call_api('POST', '/tasks_plan/', token, payload=payload)
            if status == 201 and isinstance(response, dict):
                task_data = response.get('data')
                if isinstance(task_data, dict) and task_data.get('id'):
                    return 'CREADA', task_data['id']
            # Might have persisted despite unexpected success response. Verify with GET, never resend POST.
            verified = [row for row in fetch_tasks(token, group_id=plan_id)
                        if str(row.get('description', '')).strip().casefold() == TEST_TASK.casefold()]
            if len(verified) == 1:
                return 'VERIFICADA', verified[0].get('id')
            raise ValueError('Resultado de creación no confirmado. Revisa la tarea en Fracttal; no reintentes.')
        result, error = safe_api_action(attempt_task_once)
        if error:
            st.error(error)
            st.info('No vuelvas a crearla sin revisar el plan en Fracttal. Los permisos ADD sobre Plan de tareas también deben estar habilitados.')
        else:
            state, created_id = result
            if state == 'EXISTE':
                st.warning('La tarea ya existe en el plan de prueba. No se duplicó.')
            else:
                st.success(f'Tarea de prueba {state.lower()}. ID: {created_id}. Comprueba en Fracttal sus 2 subtareas y su activador por evento.')

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
