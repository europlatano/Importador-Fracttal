"""Europlátano · V16 | diagnosis-first, staged Fracttal task-plan importer.
No asset linking, deleting, OT creation or editing. Two-stage task/subtask writes.
"""
import io
import json
import hmac
import hashlib
import re
import requests
import pandas as pd
import streamlit as st

API='https://app.fracttal.com/api'
TOKEN_URL='https://one.fracttal.com/oauth/token'
TYPES={'Texto':1, 'Verificación':4, 'Lista':7}
st.set_page_config(page_title='Importador Fracttal V16', page_icon='🛠️',layout='wide')
st.title('🛠️ Importador de planes Fracttal')
st.caption('Europlátano · V16 · diagnóstico real API · Excel → tarea → subtareas · SIN activos')
st.warning('IMPORTACIÓN REAL Y CONTROLADA. Primero inspecciona el diagnóstico. Nunca reintentes automáticamente una escritura; los activos se vinculan MANUALMENTE.')

def secret(key):
    try:return str(st.secrets.get(key,'')).strip()
    except Exception:return ''

pw=secret('APP_PASSWORD')
if not pw:st.error('Configura APP_PASSWORD en Secrets.');st.stop()
if not st.session_state.get('logged'):
    with st.form('auth'):
        entered=st.text_input('Contraseña del importador',type='password')
        submit=st.form_submit_button('Entrar')
    if submit:
        if hmac.compare_digest(entered,pw):st.session_state.logged=True;st.rerun()
        else:st.error('Contraseña incorrecta')
    st.stop()
with st.sidebar:
    st.success('Sesión protegida')
    if st.button('Cerrar sesión'):
        st.session_state.logged=False;st.rerun()
    st.caption('No captures ni compartas secretos OAuth.')

client_id,client_secret=secret('FRACTTAL_CLIENT_ID'),secret('FRACTTAL_CLIENT_SECRET')
ready=bool(client_id and client_secret)

def token():
    r=requests.post(TOKEN_URL,auth=(client_id,client_secret),data={'grant_type':'client_credentials'},timeout=25)
    if r.status_code!=200:raise RuntimeError(f'OAuth HTTP {r.status_code}')
    t=r.json().get('access_token')
    if not t:raise RuntimeError('Token OAuth ausente')
    return t

# Nunca mostramos respuestas HTTP sin filtrar: pueden incluir datos privados del tenant.
ALLOWED_ERROR_KEYS={'message','error','errors','details','validation_errors','field','path','code','type','status','statusCode','description'}
SENSITIVE_KEYS=('token','secret','authorization','client_id','client_secret','password','cookie','key','auth')
SUSPECT_FIELDS=('id_task_type_main','id_group_task','id_event','event_description','id_period_date','id_replay','value_main','triggers','subtasks','description','duration','id_priorities','stop_assets_sec')

def clean_error_text(value):
    if not isinstance(value,(str,int,float,bool)):return ''
    val=str(value)[:700]
    for credential in (client_id,client_secret):
        if credential:val=val.replace(credential,'[OCULTO]')
    val=re.sub(r'(?i)bearer\s+[\w.\-]+','Bearer [OCULTO]',val)
    val=re.sub(r'(?i)(access_token|refresh_token|client_secret|password|authorization)\s*[:=]\s*[\"\']?[^\s,}\"\']+',r'\1=[OCULTO]',val)
    val=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[correo oculto]',val)
    return val[:400]

def safe_api_error(parsed):
    values=[]
    def collect(obj,depth=0):
        if depth>3 or len(values)>10:return
        if isinstance(obj,dict):
            for key,val in obj.items():
                k=str(key)
                if any(secret in k.lower() for secret in SENSITIVE_KEYS):continue
                if k in ALLOWED_ERROR_KEYS:
                    if isinstance(val,(str,int,float,bool)):
                        t=clean_error_text(val)
                        if t:values.append(f'{k}: {t}')
                    elif isinstance(val,(dict,list)):collect(val,depth+1)
                elif k in SUSPECT_FIELDS and isinstance(val,(str,int,float)):
                    values.append(f'campo {k}: {clean_error_text(val)}')
        elif isinstance(obj,list):
            for part in obj[:8]:collect(part,depth+1)
    collect(parsed)
    return ' | '.join(values[:8])[:1200] or 'El servidor no facilitó un motivo de validación legible.'

class FracttalAPIError(RuntimeError):
    def __init__(self,method,path,status,detail):
        self.method,self.path,self.status,self.detail=method,path,status,detail
        super().__init__(f'{method} {path}: HTTP {status}. {detail}')

def api(method,path,t,payload=None,params=None):
    hdr={'Authorization':f'Bearer {t}','Accept':'application/json'}
    if payload is not None:hdr['Content-Type']='application/json'
    r=requests.request(method,API+path,headers=hdr,json=payload,params=params,timeout=35)
    try:data=r.json()
    except ValueError:data={}
    if r.status_code>=400 or (isinstance(data,dict) and data.get('success') is False):
        raise FracttalAPIError(method,path,r.status_code,safe_api_error(data))
    return r.status_code,data

def rows_of(resp,source):
    data=resp.get('data') if isinstance(resp,dict) else None
    if isinstance(data,list):return data
    if data is None and isinstance(resp,dict) and resp.get('success') is True:
        try:
            if int(resp.get('total'))==0:return []
        except (ValueError,TypeError):pass
    raise RuntimeError(f'La consulta de {source} no devolvió lista verificable. Escritura BLOQUEADA.')

def paginated(t,path,params=None):
    found=[]
    for offset in range(0,20000,100):
        p=dict(params or {},start=offset,limit=100)
        _,ans=api('GET',path,t,params=p)
        page=rows_of(ans,path)
        if any(not isinstance(item,dict) for item in page):raise RuntimeError('La consulta contiene registros inesperados.')
        found.extend(page)
        try:total=int(ans.get('total'))
        except (ValueError,TypeError):total=None
        if (total is not None and len(found)>=total) or len(page)<100:return found
    raise RuntimeError('Paginación incompleta; escritura bloqueada.')

def plans(t):return paginated(t,'/groups_tasks/')
def tasks(t,pid):
    vals=paginated(t,'/tasks/',{'id_group_task':int(pid)})
    if any(str(x.get('id_group_task'))!=str(pid) for x in vals):raise RuntimeError('Filtro de plan no respetado; escritura bloqueada.')
    return vals

def subtasks(t,tid):
    vals=paginated(t,'/tasks_subtasks/',{'id_task':int(tid)})
    if any(str(x.get('id_task'))!=str(tid) for x in vals):raise RuntimeError('Filtro de tarea no respetado; escritura bloqueada.')
    return vals

def match(rows,name):
    return [x for x in rows if str(x.get('description','')).strip().casefold()==name.strip().casefold()]

def fmt_error(ex):
    if isinstance(ex,requests.Timeout):return 'Tiempo de espera agotado. Comprueba Fracttal ANTES de repetir.'
    if isinstance(ex,requests.RequestException):return 'Error de red. Comprueba Fracttal ANTES de repetir.'
    return str(ex)

st.header('1 · Archivo Excel')
f=st.file_uploader('Carga el Excel de mantenimiento (.xlsx)',type=['xlsx'])
if f is None:
    st.info('Selecciona la plantilla Reisopack 2904 para empezar. No se realizan cambios en Fracttal al cargarla.')
    st.stop()
raw=f.getvalue();file_hash=hashlib.sha256(raw).hexdigest()
try:
    book=pd.read_excel(io.BytesIO(raw),sheet_name=None,dtype=str,keep_default_na=False)
except Exception:
    st.error('No se pudo abrir el Excel.');st.stop()
required={'PLANES':['ID_PLAN','NOMBRE_PLAN'],'TAREAS':['ID_TAREA','ID_PLAN','NOMBRE_TAREA','TIPO_INICIAL','ACTIVADOR','INTERVALO','UNIDAD_INTERVALO'], 'SUBTAREAS':['ID_TAREA','ORDEN','TIPO_SUBTAREA','GRUPO_PARTE','DESCRIPCION','OBLIGATORIA','ADJUNTO_OBLIGATORIO']}
problems=[]
for sh,cols in required.items():
    if sh not in book:problems.append(f'Falta hoja {sh}')
    else:problems.extend(f'Falta {sh}.{c}' for c in cols if c not in book[sh].columns)
if problems:st.error(' / '.join(problems));st.stop()
ps,ts,ss=book['PLANES'].copy(),book['TAREAS'].copy(),book['SUBTAREAS'].copy()
for df in [ps,ts,ss]:df.fillna('',inplace=True)
if ps['ID_PLAN'].duplicated().any():problems.append('IDs de plan duplicados')
if ts['ID_TAREA'].duplicated().any():problems.append('IDs de tarea duplicados')
if set(ts.ID_PLAN)-set(ps.ID_PLAN):problems.append('Tareas sin plan')
if set(ss.ID_TAREA)-set(ts.ID_TAREA):problems.append('Subtareas sin tarea')
if ss[['ID_TAREA','ORDEN']].duplicated().any():problems.append('Órdenes duplicados por tarea')
for i,r in ts.iterrows():
    if r.ACTIVADOR not in ('Fecha','Evento'):problems.append(f'{r.ID_TAREA}: activador inválido')
    if len(str(r.NOMBRE_TAREA).strip())<3 or len(str(r.NOMBRE_TAREA))>200:problems.append(f'{r.ID_TAREA}: nombre incorrecto')
    if r.ACTIVADOR=='Fecha':
        try:
            if int(r.INTERVALO)<1:raise ValueError
        except (ValueError,TypeError):problems.append(f'{r.ID_TAREA}: intervalo no válido')
        if not str(r.UNIDAD_INTERVALO).strip():problems.append(f'{r.ID_TAREA}: falta unidad')
for i,r in ss.iterrows():
    if r.TIPO_SUBTAREA not in TYPES:problems.append(f'Subtarea {r.ID_TAREA}-{r.ORDEN}: tipo desconocido')
    if not str(r.GRUPO_PARTE).strip():problems.append(f'Subtarea {r.ID_TAREA}-{r.ORDEN}: falta Grupo/Parte')
    if not str(r.DESCRIPCION).strip():problems.append(f'Subtarea {r.ID_TAREA}-{r.ORDEN}: falta descripción')
    if str(r.OBLIGATORIA).strip().casefold() not in ('sí','si','no'):problems.append(f'Subtarea {r.ID_TAREA}-{r.ORDEN}: OBLIGATORIA inválida')
    if str(r.ADJUNTO_OBLIGATORIO).strip().casefold() not in ('sí','si','no'):problems.append(f'Subtarea {r.ID_TAREA}-{r.ORDEN}: ADJUNTO_OBLIGATORIO inválido')
    if r.TIPO_SUBTAREA=='Lista' and len([v for v in str(r.get('OPCIONES_RESPUESTA','')).split('|') if v.strip()])<2:problems.append(f'Lista {r.ID_TAREA}-{r.ORDEN}: faltan opciones')
    try:
        if int(r.ORDEN)<1:raise ValueError
    except (ValueError,TypeError):problems.append(f'Subtarea {r.ID_TAREA}-{r.ORDEN}: orden inválido')
a,b,c,d=st.columns(4)
for widget,label,num in zip((a,b,c,d),('Planes','Tareas','Subtareas','Incidencias'),(len(ps),len(ts),len(ss),len(problems))):widget.metric(label,num)
if problems:
    st.error('Errores de validación:');st.write(problems);st.stop()
st.success('Estructura del Excel validada. Los campos Grupo/Parte y Adjunto obligatorio serán incluidos al crear subtareas.')
if len(ps)!=1:
    st.error('V13 trabaja con un único plan por archivo para evitar confusiones.');st.stop()
plan_name=str(ps.iloc[0]['NOMBRE_PLAN']).strip()
with st.expander('Ver tareas y subtareas'):
    st.dataframe(ts,use_container_width=True,hide_index=True)
    st.dataframe(ss,use_container_width=True,hide_index=True)

st.header('2 · Conexión y correspondencias')
if not ready:
    st.error('Faltan FRACTTAL_CLIENT_ID o FRACTTAL_CLIENT_SECRET en Secrets de Streamlit.');st.stop()
if st.button('🔗 Consultar tipos y planes de Fracttal (solo lectura)'):
    try:
        t=token();all_plans=plans(t);observed=paginated(t,'/tasks/')
        options=sorted({(str(r.get('tasks_types_main_description','')).strip(),int(r['id_task_type_main'])) for r in observed if r.get('tasks_types_main_description') and str(r.get('id_task_type_main','')).isdigit()},key=lambda x:(x[0].casefold(),x[1]))
        st.session_state.v12_types=options
        st.session_state.v12_plans=all_plans
        st.success(f'Consulta correcta: {len(all_plans)} planes y {len(options)} tipos observados.')
    except Exception as exc:st.error(fmt_error(exc))
choices=st.session_state.get('v12_types',[])
if not choices:st.info('Pulsa «Consultar tipos y planes» para cargar los desplegables.');st.stop()
selected_types={}
for name in sorted(set(ts.TIPO_INICIAL)):
    select=st.selectbox(f'Tipo del Excel: {name} → Fracttal',options=[None]+choices,format_func=lambda x:'— Elige tipo —' if x is None else f'{x[0]} (ID {x[1]})',key='map_'+name)
    if select:selected_types[name]=select
if len(selected_types)!=len(set(ts.TIPO_INICIAL)):
    st.warning('Selecciona un tipo principal para todas las clasificaciones del Excel.');st.stop()

st.subheader('Configurar períodos y eventos')
st.info('Selecciona las frecuencias por su NOMBRE. El importador solo las habilita si obtiene una correspondencia verificable desde Fracttal. Nunca presupone los códigos internos.')

# En esta versión únicamente se aceptan correspondencias de periodicidad respaldadas
# por la descripción de unidad devuelta en la respuesta de Fracttal.
def normaliza_unidad(valor):
    raw=str(valor or '').strip().casefold()
    known={'days':'Día','day':'Día','días':'Día','día':'Día','dias':'Día',
           'weeks':'Semana','week':'Semana','semanas':'Semana','semana':'Semana',
           'months':'Mes','month':'Mes','meses':'Mes','mes':'Mes',
           'years':'Año','year':'Año','años':'Año','año':'Año'}
    return known.get(raw, '')

with st.expander('🔎 Consultar frecuencias existentes (solo lectura)', expanded=True):
    st.caption('Buscamos referencias reales de Fracttal para relacionar Semana y Mes con sus códigos. Si la API no devuelve unidades identificables, se bloquearán las tareas por fecha, pero podrás trabajar con las de Evento.')
    if st.button('Consultar frecuencias de Fracttal (SOLO LECTURA)'):
        try:
            sample=paginated(token(),'/tasks_triggers/')
            refs=[]
            for row in sample:
                if not isinstance(row,dict):continue
                try:pid=int(row.get('id_period_date'))
                except (TypeError,ValueError):continue
                name=''
                for key in ('period_dates_description','period_date_description','period_description','period_date','period_dates'):
                    if isinstance(row.get(key),str) and row[key].strip():name=row[key].strip();break
                label=normaliza_unidad(name)
                if label:
                    refs.append({'ID':pid,'Frecuencia':label,'Nombre original':name,'Tarea':row.get('id_task') or row.get('id_tasks')})
            st.session_state.v14_refs=refs
            if refs:st.success(f'Referencias verificables encontradas: {len(refs)}.')
            else:st.warning('La API no devolvió referencias verificables de unidades de periodicidad. No se asignarán números por suposición.')
        except Exception:
            st.session_state.v14_refs=[]
            st.warning('La API no ha proporcionado el catálogo de frecuencias mediante esta consulta. No se ha modificado Fracttal.')
    refs=st.session_state.get('v14_refs',[])
    if refs:
        st.dataframe(pd.DataFrame(refs)[['Frecuencia','Nombre original','ID','Tarea']].drop_duplicates(),hide_index=True,use_container_width=True)

period_map={}
for excel_unit in sorted(set(ts.loc[ts.ACTIVADOR=='Fecha','UNIDAD_INTERVALO'])):
    label=normaliza_unidad(excel_unit)
    candidates=sorted({r['ID'] for r in st.session_state.get('v14_refs',[]) if r['Frecuencia']==label})
    if not label:
        st.error(f'Frecuencia del Excel no reconocida: {excel_unit!r}.')
        period_map[excel_unit]=0
    elif len(candidates)==1:
        st.success(f'{excel_unit} → {label} (referencia identificada en Fracttal).')
        accepted=st.checkbox(f'Confirmo utilizar la frecuencia «{label}» para las tareas del Excel',key='v14_accepted_'+excel_unit)
        period_map[excel_unit]=candidates[0] if accepted else 0
    elif len(candidates)>1:
        st.warning(f'Fracttal ha devuelto varios códigos para «{label}». No se selecciona ninguno automáticamente.')
        st.caption('Es necesario verificar el catálogo con soporte de Fracttal antes de importar por fecha.')
        period_map[excel_unit]=0
    else:
        st.warning(f'No hay referencia comprobada para «{label}». Las tareas por fecha que utilicen esa frecuencia permanecerán deshabilitadas.')
        period_map[excel_unit]=0
if any(x==0 for x in period_map.values()):
    st.info('Puedes seguir preparando y revisando las tareas por EVENTO. No se crearán tareas por FECHA con unidades desconocidas.')
events={}
for _,r in ts[ts.ACTIVADOR=='Evento'].iterrows():
    events[r.ID_TAREA]=st.text_input(f'Evento para {r.ID_TAREA}: {r.NOMBRE_TAREA}',value=str(r.NOMBRE_TAREA),key='ev_'+r.ID_TAREA)

st.header('3 · Elegir plan y tarea')
found=match(st.session_state.get('v12_plans',[]),plan_name)
if len(found)>1:st.error('Existen varios planes con el mismo nombre. Se bloquea la importación.');st.stop()
if found:
    plan_id=int(found[0]['id']);st.success(f'Plan ya existente: {plan_name} · ID {plan_id}')
else:
    st.warning('El plan no existe en los resultados consultados. Puedes crearlo antes de importar las tareas.')
    st.code(plan_name,language=None)
    ok=st.checkbox('Confirmo crear este plan REAL sin vincular activos.')
    phrase=st.text_input('Escribe CREAR PLAN para autorizar',key='create_plan_phrase')
    if st.button('Crear plan en Fracttal',disabled=not(ok and phrase=='CREAR PLAN')):
        try:
            t=token();fresh=match(plans(t),plan_name)
            if fresh:st.warning('Plan ya existe. Actualiza consulta.');st.stop()
            code,response=api('POST','/groups_tasks/',t,{'description':plan_name})
            st.success(f'Petición enviada (HTTP {code}). Comprueba el nuevo plan y vuelve a consultar. No repitas la creación.')
            st.session_state.v12_plans=plans(t)
        except Exception as exc:st.error(fmt_error(exc))
    st.stop()

labels=[f'{r.ID_TAREA} · {r.NOMBRE_TAREA}' for _,r in ts.iterrows()]
choice=st.selectbox('Selecciona UNA tarea que quieras importar',range(len(labels)),format_func=lambda i:labels[i])
r=ts.iloc[choice]
subrows=ss[ss.ID_TAREA==r.ID_TAREA].copy()
subrows['_order']=subrows.ORDEN.astype(int)
subrows=subrows.sort_values('_order')

def yes(value):return str(value).strip().casefold() in ('sí','si')

def mk_sub(row):
    result={'description':str(row.DESCRIPCION).strip(), 'id_task_form_item_type':TYPES[row.TIPO_SUBTAREA], 'is_required':yes(row.OBLIGATORIA),'attachments_required':yes(row.ADJUNTO_OBLIGATORIO),'task_form_item_group_description':str(row.GRUPO_PARTE).strip()}
    if row.TIPO_SUBTAREA=='Lista':result['dropdown_options']=[{'description':x.strip()} for x in str(row.get('OPCIONES_RESPUESTA','')).split('|') if x.strip()]
    return result

# Mimic the V11 successfully tested payload exactly: subtasks is present but empty.
# The complete checklist is added only in the second POST /tasks_subtasks/.
payload={'description':str(r.NOMBRE_TAREA).strip(),'id_task_type_main':selected_types[r.TIPO_INICIAL][1],'id_group_task':plan_id,'triggers':[],'subtasks':[]}
if r.ACTIVADOR=='Fecha':
    val=period_map.get(r.UNIDAD_INTERVALO,0)
    if val:payload['triggers']=[{'id_task_trigger_type':1,'value_main':int(r.INTERVALO),'id_period_date':val,'id_replay':1}]
else:
    payload['triggers']=[{'id_task_trigger_type':4,'event_description':events[r.ID_TAREA].strip()}]
# Deliberately do not send duration here: V11 accepted the minimal schema without it.
# Task duration can be configured later once independently verified.
subs=[mk_sub(row) for _,row in subrows.iterrows()]
st.write(f'**{len(subs)} subtareas**, {r.ACTIVADOR}, **sin activos**. Grupo/Parte y adjuntos se enviarán con cada subtarea.')
st.caption('V16: la solicitud mínima para EVENTO reproduce exactamente los nombres de campos de la V11. Si vuelve a dar HTTP 400, el error mostrará una descripción filtrada del servidor. No confundimos similitud de formato con validez de un evento de catálogo.')
with st.expander('Vista previa de tarea (POST /tasks_plan/)',expanded=False):st.json(payload)
with st.expander('Vista previa de subtareas (POST /tasks_subtasks/)',expanded=False):st.json({'id_task':'<se obtendrá al crear tarea>','is_cyclical':False,'subtasks':subs})

st.subheader('🔬 Diagnóstico comparativo sin escribir')
st.info('V11 y V15 utilizan el MISMO esquema mínimo de creación por evento. La diferencia real puede estar en el VALOR del evento: Fracttal exige resolverlo en su catálogo. La API podría devolver HTTP 400 si ese nombre no existe. No se enviará ninguna petición de escritura durante este diagnóstico.')
expected={'description','id_task_type_main','id_group_task','triggers','subtasks'}
extra=set(payload)-expected
st.write('Campos mínimos esperados (según V11):', ', '.join(sorted(expected)))
st.write('Campos adicionales en el POST seleccionado:', ', '.join(sorted(extra)) or 'Ninguno')
if r.ACTIVADOR=='Evento':
    st.write('Evento que debe existir en el catálogo o resolverse por la API:', events[r.ID_TAREA].strip())
    st.warning('Si el error 400 menciona evento inexistente: crea o selecciona ese evento en el catálogo de Fracttal ANTES de volver a probar. No sustituiremos silenciosamente el activador por uno genérico.')

# Huella de los datos que se han inspeccionado para invalidar comprobaciones previas
# cuando cambia el tipo, el evento o el activador.
payload_digest=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


st.subheader('Comprobación de duplicados (solo lectura)')
if st.button('🔎 Consultar tareas y subtareas del plan'):
    try:
        t=token();existing=match(tasks(t,plan_id),payload['description'])
        st.session_state.v12_check=(file_hash,plan_id,r.ID_TAREA,payload_digest,existing)
        if len(existing)>1:st.error('Más de una tarea con el mismo nombre. Se bloquea la importación.')
        elif len(existing)==1:
            st.info(f'La tarea YA existe (ID {existing[0]["id"]}). No se duplicará. Puedes verificar si le faltan subtareas.')
        else:st.success('No existe una tarea con este nombre en el plan. Lista para prueba de creación.')
    except Exception as exc:
        st.session_state.pop('v12_check',None);st.error(fmt_error(exc))

check=st.session_state.get('v12_check')
check_valid=bool(check and check[:4]==(file_hash,plan_id,r.ID_TAREA,payload_digest))
existing=check[4] if check_valid else None

st.subheader('4 · Ejecutar un paso controlado')
st.caption('El proceso se divide en dos operaciones: 1) crear tarea con activador y 2) añadir subtareas con su Grupo/Parte y adjunto obligatorio. Así utilizamos el endpoint que admite nombres de Grupo/Parte. Si algo falla, NO repetimos automáticamente.')
confirm=st.checkbox('He comprobado los datos y entiendo que se modificará Fracttal.',key='v12_confirm')
phrase=st.text_input('Escribe IMPORTAR TAREA para autorizar',key='v12_phrase')
allowed=bool(confirm and phrase=='IMPORTAR TAREA' and check_valid)

if existing is not None and len(existing)>1:st.error('Duplicidad de tareas, bloqueado.');st.stop()
attempt_key=f'attempt_v16_{plan_id}_{payload_digest}'
if st.session_state.get(attempt_key):
    st.warning('Ya se ha intentado este POST durante la sesión. No se permite pulsarlo dos veces. Comprueba el plan en Fracttal. Tras comprobar el resultado, reinicia sesión para un nuevo intento. Si continúa el HTTP 400, NO repitas: facilita el mensaje filtrado.')
if existing is not None and len(existing)==0:
    if st.button('1️⃣ Crear solo ESTA tarea (sin subtareas)',disabled=not (allowed and payload['triggers']) and not st.session_state.get(attempt_key)):
        st.session_state[attempt_key]=True
        try:
            t=token()
            current=match(tasks(t,plan_id),payload['description'])
            if current:st.warning('Tarea ya existente, no se creó otra. Vuelve a consultar.')
            else:
                code,out=api('POST','/tasks_plan/',t,payload)
                data=out.get('data') if isinstance(out,dict) else None
                ident=data.get('id') if isinstance(data,dict) else None
                st.session_state.pop('v12_check',None)
                if ident:st.success(f'Tarea creada (ID {ident}). Ahora pulsa Consultar tareas y subtareas para habilitar el paso 2.')
                else:st.warning(f'HTTP {code} sin ID verificable. Comprueba Fracttal y vuelve a consultar; NO repitas la escritura.')
        except Exception as exc:
            st.session_state.pop('v12_check',None)
            safe_msg=fmt_error(exc)
            st.error(safe_msg)
            st.warning('No reintentes la escritura. Comprueba Fracttal y comparte ÚNICAMENTE este mensaje filtrado, sin credenciales.')
            if isinstance(exc,FracttalAPIError):
                st.code(safe_msg,language=None)
                st.download_button('Descargar diagnóstico sin secretos',data=json.dumps({'version':'16','http_status':exc.status,'endpoint':exc.path,'method':exc.method,'detalle_filtrado':exc.detail,'campos_enviados':sorted(payload.keys()),'tipo_activador':r.ACTIVADOR,'plan_id':plan_id},ensure_ascii=False,indent=2),file_name='diagnostico_fracttal_v16.json',mime='application/json')

if existing is not None and len(existing)==1:
    tid=int(existing[0]['id'])
    if st.button('🔎 Verificar subtareas existentes de ESTA tarea'):
        try:
            existing_subs=subtasks(token(),tid)
            st.session_state.v12_subcheck=(file_hash,plan_id,r.ID_TAREA,payload_digest,tid,existing_subs)
            st.success(f'Subtareas existentes: {len(existing_subs)}')
        except Exception as exc:
            st.session_state.pop('v12_subcheck',None);st.error(fmt_error(exc))
    subcheck=st.session_state.get('v12_subcheck')
    if subcheck and subcheck[:5]==(file_hash,plan_id,r.ID_TAREA,payload_digest,tid):
        old=subcheck[5]
        if old:
            st.warning(f'Hay {len(old)} subtarea(s) en la tarea. No se permiten cargas automáticas adicionales para evitar duplicaciones o mezclar órdenes. Comprueba manualmente.')
        else:
            st.success('La tarea NO tiene subtareas. Se puede crear el bloque completo una sola vez.')
            if st.button('2️⃣ Crear todas las subtareas de ESTA tarea',disabled=not(allowed and len(subs)>0)):
                try:
                    t=token()
                    fresh=match(tasks(t,plan_id),payload['description'])
                    if len(fresh)!=1 or int(fresh[0]['id'])!=tid:raise RuntimeError('La tarea cambió. Escritura bloqueada.')
                    if subtasks(t,tid):raise RuntimeError('Ya hay subtareas. Escritura bloqueada.')
                    code,out=api('POST','/tasks_subtasks/',t,{'id_task':tid,'is_cyclical':False,'subtasks':subs})
                    st.session_state.pop('v12_subcheck',None)
                    st.session_state.pop('v12_check',None)
                    st.success(f'Petición de subtareas enviada (HTTP {code}). Comprueba cada Grupo/Parte, lista y adjunto obligatorio en Fracttal.')
                    st.info('No repetir. Vuelve a consultar tareas y subtareas para confirmar.')
                except Exception as exc:
                    st.session_state.pop('v12_subcheck',None)
                    st.error(fmt_error(exc));st.warning('Comprueba Fracttal antes de repetir; puede haber una escritura parcial.')

st.divider()
st.caption('V16 no asocia activos, no modifica ni elimina planes/tareas, no crea OT y no guarda secretos en el Excel. Importación deliberadamente por tarea con verificación, no masiva de una sola pulsación.')
