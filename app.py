import io
import pandas as pd
import streamlit as st

st.set_page_config(page_title='Importador Fracttal | Demo', page_icon='🛠️', layout='wide')
st.title('🛠️ Importador de planes Fracttal')
st.caption('Europlátano · Demostración de lectura y validación · SIN conexión a Fracttal')
st.info('Modo seguro: esta demostración no solicita claves, no utiliza API y no modifica ningún dato en Fracttal.')

uploaded = st.file_uploader('Selecciona la plantilla Excel (.xlsx)', type=['xlsx'])
if uploaded is None:
    st.markdown('Carga el Excel de la Reisopack 2904 para visualizar sus planes, tareas y subtareas.')
    st.stop()

try:
    workbook = pd.read_excel(io.BytesIO(uploaded.getvalue()), sheet_name=None, dtype=str, keep_default_na=False)
except Exception as exc:
    st.error(f'No se pudo leer el archivo: {exc}')
    st.stop()

required = {'PLANES': ['ID_PLAN', 'NOMBRE_PLAN'], 'TAREAS': ['ID_TAREA', 'ID_PLAN', 'NOMBRE_TAREA', 'ACTIVADOR'], 'SUBTAREAS': ['ID_TAREA', 'ORDEN', 'TIPO_SUBTAREA', 'DESCRIPCION']}
errors = []
for sheet, columns in required.items():
    if sheet not in workbook:
        errors.append(f'Falta la hoja {sheet}.')
    else:
        for column in columns:
            if column not in workbook[sheet].columns:
                errors.append(f'Falta la columna {column} en {sheet}.')
if errors:
    for error in errors:
        st.error(error)
    st.stop()

plans, tasks, subtasks = (workbook[k].copy() for k in ('PLANES', 'TAREAS', 'SUBTAREAS'))
for col in ('ID_PLAN', 'NOMBRE_PLAN'):
    if plans[col].str.strip().eq('').any(): errors.append(f'PLANES: hay valores vacíos en {col}.')
for col in ('ID_TAREA', 'ID_PLAN', 'NOMBRE_TAREA', 'ACTIVADOR'):
    if tasks[col].str.strip().eq('').any(): errors.append(f'TAREAS: hay valores vacíos en {col}.')
if plans.ID_PLAN.duplicated().any(): errors.append('Hay ID_PLAN duplicados.')
if tasks.ID_TAREA.duplicated().any(): errors.append('Hay ID_TAREA duplicados.')
unknown_plan = set(tasks.ID_PLAN) - set(plans.ID_PLAN)
if unknown_plan: errors.append('Tareas que apuntan a planes inexistentes: ' + ', '.join(sorted(unknown_plan)))
unknown_task = set(subtasks.ID_TAREA) - set(tasks.ID_TAREA)
if unknown_task: errors.append('Subtareas que apuntan a tareas inexistentes: ' + ', '.join(sorted(unknown_task)))
if not subtasks.empty:
    if subtasks[['ID_TAREA','ORDEN']].duplicated().any(): errors.append('Hay órdenes de subtarea duplicados dentro de una tarea.')
    if subtasks.DESCRIPCION.str.strip().eq('').any(): errors.append('Hay subtareas sin descripción.')
for val in tasks.ACTIVADOR.unique():
    if val not in ('Fecha','Evento'): errors.append(f'Activador no reconocido: {val}')

c1,c2,c3,c4 = st.columns(4)
c1.metric('Planes', len(plans)); c2.metric('Tareas', len(tasks)); c3.metric('Subtareas', len(subtasks)); c4.metric('Incidencias', len(errors))
if errors:
    st.error('La validación ha detectado incidencias:')
    for error in errors: st.write('• ' + error)
else:
    st.success('Validación estructural correcta. Esto NO confirma compatibilidad con los endpoints de Fracttal.')

st.subheader('Vista previa')
for name, frame in [('Planes',plans),('Tareas',tasks),('Subtareas',subtasks)]:
    with st.expander(f'{name} ({len(frame)})', expanded=(name=='Tareas')):
        st.dataframe(frame, use_container_width=True, hide_index=True)

st.subheader('Detalle por tarea')
if not tasks.empty:
    opts = [f"{row.ID_TAREA} · {row.NOMBRE_TAREA}" for row in tasks.itertuples()]
    chosen = st.selectbox('Selecciona una tarea',opts)
    task_id = chosen.split(' · ')[0]
    st.dataframe(subtasks[subtasks.ID_TAREA==task_id],use_container_width=True,hide_index=True)

st.divider()
st.button('Importar a Fracttal (no disponible en la demo)', disabled=True, use_container_width=True)
st.caption('La conexión real requerirá verificar endpoints, permisos y guardar las credenciales en secretos del servidor. Nunca se incluirán en GitHub ni en el Excel.')
