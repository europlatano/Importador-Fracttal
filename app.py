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
st.set_page_config(page_title='Importador Fracttal AUTOMÁTICO', page_icon='🛠️',layout='wide')
st.title('🛠️ Importador de planes Fracttal')
st.caption('Europlátano · Importador automático por Excel · SIN activos · tipos y frecuencias precargados')
st.info('Los tipos y frecuencias proceden del Excel. Revisa un resumen global y autoriza una vez. No se vinculan activos ni se crean OT.')

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


# -------- IMPORTACION AUTOMATICA CONSOLIDADA --------
import unicodedata

def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFD', str(value).casefold().strip()) if unicodedata.category(c) != 'Mn')

def unit_name(value):
    v=norm(value)
    map_names={'days':'Día','day':'Día','dia':'Día','dias':'Día','daily':'Día',
               'weeks':'Semana','week':'Semana','semana':'Semana','semanas':'Semana',
               'months':'Mes','month':'Mes','mes':'Mes','meses':'Mes',
               'years':'Año','year':'Año','ano':'Año','anos':'Año'}
    return map_names.get(v)

def positive_int(v):
    try:
        z=int(v)
        return z if z > 0 else None
    except (ValueError,TypeError):return None

def discover_freq(t):
    records=[]
    # Consultas de solo lectura: el nombre y el ID deben proceder del MISMO registro.
    # No deducimos los IDs por el orden del desplegable visual.
    for path in ('/tasks_triggers/',):
        try:
            for r in paginated(t,path):
                if not isinstance(r,dict):continue
                pid=positive_int(r.get('id_period_date'))
                if not pid:continue
                for key in ('period_dates_description','period_date_description','period_description',
                            'period_date','period_dates','frequency_description'):
                    if r.get(key) and isinstance(r.get(key),str):
                        unit=unit_name(r[key])
                        if unit:
                            records.append({'Unidad':unit,'ID':pid,'Fuente':path})
                        break
        except Exception:
            continue
    mapping={}
    for name in ('Día','Semana','Mes','Año'):
        ids={r['ID'] for r in records if r['Unidad']==name}
        if len(ids)==1:mapping[name]=ids.pop()
    return records,mapping

def extract_id(out):
    data=out.get('data') if isinstance(out,dict) else None
    if isinstance(data,dict):return positive_int(data.get('id'))
    return None

def required_event_name(name,task_name):
    return (name or task_name).strip()

st.header('2 · Conectar y preparar')
if not ready:
    st.error('Configura FRACTTAL_CLIENT_ID y FRACTTAL_CLIENT_SECRET en los secretos de Streamlit.');st.stop()
if st.button('🔄 Actualizar datos de Fracttal (solo lectura)',type='primary'):
    try:
        access=token()
        allplans=plans(access)
        alltasktypes=paginated(access,'/tasks/')
        candidates=sorted({(str(x.get('tasks_types_main_description','')).strip(),int(x['id_task_type_main']))
            for x in alltasktypes if str(x.get('id_task_type_main','')).isdigit() and x.get('tasks_types_main_description')},key=lambda x:(norm(x[0]),x[1]))
        refs,freq=discover_freq(access)
        st.session_state.final_data={'plans':allplans,'types':candidates,'freq':freq,'refs':refs}
        st.success(f'{len(allplans)} planes, {len(candidates)} tipos y {len(freq)} frecuencias identificadas por nombre.')
    except Exception as ex:
        st.error(fmt_error(ex))
        st.session_state.pop('final_data',None)
base=st.session_state.get('final_data')
if not base:
    st.info('Pulsa «Actualizar datos de Fracttal» para comenzar.');st.stop()

matched=match(base['plans'],plan_name)
if len(matched)>1:
    st.error('Hay varios planes con el mismo nombre. Para evitar errores se bloquea la importación.');st.stop()
if matched:
    plan_id=positive_int(matched[0].get('id'))
    st.success(f'Plan existente: {plan_name} · ID {plan_id}')
else:
    st.warning('El plan no existe en los resultados actuales.')
    st.code(plan_name)
    st.checkbox('Confirmo la creación del plan real, sin activos',key='approve_plan')
    st.text_input('Para crear el plan escribe CREAR PLAN',key='approve_plan_text')
    if st.button('Crear plan',disabled=not(st.session_state.get('approve_plan') and st.session_state.get('approve_plan_text')=='CREAR PLAN')):
        try:
            access=token()
            if match(plans(access),plan_name):
                st.warning('El plan ya existe. Actualiza los datos.')
            else:
                _,out=api('POST','/groups_tasks/',access,{'description':plan_name})
                st.success('Solicitud de creación enviada. Actualiza los datos y verifica el plan.')
        except Exception as ex:st.error(fmt_error(ex))
    st.stop()
if not plan_id:st.error('Plan sin identificador válido');st.stop()

st.subheader('Tipos de tarea: asignación automática')
st.caption('Tipos existentes en Europlátano: Correctivo, Mejora, Predictivo y Preventivo, cada uno Interno/Externo. El Excel indica la clase. Se selecciona automáticamente la variante Interno por defecto; no tienes que elegir una por tarea.')
KNOWN_LABELS=['Correctivo externo','Correctivo interno','Mejora externa','Mejora interna',
              'Predictivo externo','Predictivo interno','Preventivo externo','Preventivo interno']

# Un identificador de tipo se acepta automáticamente solo si el ID y el nombre
# proceden del mismo registro real devuelto por la API del tenant.
def auto_type_choice(src, real_types):
    q=norm(src)
    exact=[(name,id_) for name,id_ in real_types if norm(name)==q]
    if len(exact)==1:return exact[0], 'Coincidencia exacta'
    variant='externo' if 'extern' in q else 'interno'
    family=next((x for x in ['preventivo','correctivo','predictivo','mejora'] if x in q),None)
    possible=[(name,id_) for name,id_ in real_types
              if family and family in norm(name) and variant in norm(name)]
    if len(possible)==1:return possible[0], 'Correspondencia automática'
    return None, 'No resuelto o ambiguo'

chosen_types={}
type_overrides={}
for typ in sorted(set(ts.TIPO_INICIAL)):
    picked,state=auto_type_choice(typ,base['types'])
    if picked:chosen_types[typ]=picked
    type_overrides[typ]=state
with st.expander('Ver tipos disponibles en Europlátano y cambiar correspondencias (opcional)'):
    st.write('Tipos esperados: '+', '.join(KNOWN_LABELS))
    choices=base['types']
    for typ in sorted(set(ts.TIPO_INICIAL)):
        picked=chosen_types.get(typ)
        foundidx=next((i+1 for i,x in enumerate(choices) if picked and x==picked),0)
        chosen=st.selectbox(f'{typ} en Excel', [None]+choices,index=foundidx,
            format_func=lambda v:'Sin asignar' if v is None else f'{v[0]} (ID {v[1]})',key='mapping_'+typ)
        if chosen:chosen_types[typ]=chosen
        else:chosen_types.pop(typ,None)
summary_types=pd.DataFrame([{'Tipo en Excel':typ,
                             'Tipo en Fracttal':chosen_types.get(typ,('SIN RESOLVER',))[0],
                             'Origen':type_overrides[typ]}
                             for typ in sorted(set(ts.TIPO_INICIAL))])
st.dataframe(summary_types,hide_index=True,use_container_width=True)
if len(chosen_types)!=len(set(ts.TIPO_INICIAL)):
    st.error('Faltan tipos principales en los registros recuperados de Fracttal. No se inventarán IDs. Consulta el catálogo o elige la correspondencia excepcional en el apartado anterior.')
    st.stop()

st.header('3 · Activadores: configuración tomada del Excel')
st.caption('La frecuencia es el selector propio del activador Fecha (días, semanas, meses o años). No es un catálogo de unidades de lectura, ni se elige para cada tarea.')

# La API pide un ID numérico para la opción de un desplegable fijo de Fracttal.
# Recuperamos las equivalencias cuando los datos de la API las incluyen.
# El ejemplo público de la API corresponde a 1 mes y id_period_date=2,
# pero no lo usamos como prueba universal para todas las unidades.
needs=sorted({unit_name(x) for x in ts.loc[ts.ACTIVADOR=='Fecha','UNIDAD_INTERVALO'] if unit_name(x)})
settings={}
for _,r in ts.iterrows():
    tid=str(r.ID_TAREA)
    if r.ACTIVADOR=='Fecha':
        un=unit_name(r.UNIDAD_INTERVALO)
        settings[tid]={'kind':'Fecha','unit':un,'amount':int(r.INTERVALO),
                       'replay':'Siempre','repetitions':None,'fixed':None}
    else:
        settings[tid]={'kind':'Evento','event':None}

st.subheader('Tareas por fecha')
date_rows=[]
for _,r in ts[ts.ACTIVADOR=='Fecha'].iterrows():
    x=settings[str(r.ID_TAREA)]
    date_rows.append({'Tarea':str(r.ID_TAREA),'Descripción':str(r.NOMBRE_TAREA),
                      'Hacer cada':x['amount'],'Frecuencia':x['unit'],
                      'Repetir':'Siempre','Programación fija':'Pendiente de API'})
if date_rows:st.dataframe(pd.DataFrame(date_rows),hide_index=True,use_container_width=True)
if any(x['unit'] is None for x in settings.values() if x['kind']=='Fecha'):
    st.error('El Excel incluye una frecuencia por fecha no reconocida.');st.stop()

with st.expander('Cambiar periodicidad o repetición (solo si necesitas modificar el Excel)'):
    for _,r in ts[ts.ACTIVADOR=='Fecha'].iterrows():
        tid=str(r.ID_TAREA); x=settings[tid]
        st.write(f'{tid}: {r.NOMBRE_TAREA}')
        c1,c2,c3=st.columns(3)
        x['amount']=int(c1.number_input('Hacer cada',1,999,x['amount'],key='amount_'+tid))
        x['unit']=c2.selectbox('Frecuencia',['Día','Semana','Mes','Año'],
            index=['Día','Semana','Mes','Año'].index(x['unit']),key='unit_'+tid)
        x['replay']=c3.selectbox('Repetir',['Siempre','Por un número de veces'],key='replay_'+tid)
        x['repetitions']=(int(st.number_input('Número de veces',1,99999,1,key='count_'+tid))
                           if x['replay']!='Siempre' else None)
        st.caption('Programación fija: la API documentada no incluye todavía un campo confirmado; deberá revisarse en Fracttal. No se inventará su valor.')

# No imponemos una lista cerrada de ID numéricos: una referencia solo vale
# cuando el registro de la API aporta nombre y código conjuntamente.
frequency_map=dict(base['freq'])
if frequency_map:
    st.caption('Frecuencias detectadas automáticamente a partir de datos recuperados de Fracttal: '+
               ', '.join(sorted(frequency_map)))
missing_freq=[x for x in needs if x not in frequency_map]
if missing_freq:
    st.warning('La API no ha facilitado todavía el código de '+', '.join(missing_freq)+
               '. Las tareas por evento pueden importarse. Las tareas por fecha quedarán pendientes: no hay que escribir códigos manuales ni adivinarlos.')

suggestions={
'T04':'AVERÍA O DESAJUSTE DEL CABEZAL DE FLEJADO',
'T05':'AVERÍA DE CILINDROS NEUMÁTICOS',
'T06':'FALLO DEL SISTEMA DE CANTONERAS',
'T07':'FALLO DE ALIMENTACIÓN DE FLEJE',
'T08':'AVERÍA O DESAJUSTE DE SENSORES',
'T09':'INCIDENCIA MECÁNICA',
'T10':'INCIDENCIA ELÉCTRICA O DE AUTOMATIZACIÓN'}
for _,r in ts[ts.ACTIVADOR=='Evento'].iterrows():
    tid=str(r.ID_TAREA)
    settings[tid]['event']=suggestions.get(tid,str(r.NOMBRE_TAREA))
with st.expander('Editar eventos propuestos (opcional)'):
    for _,r in ts[ts.ACTIVADOR=='Evento'].iterrows():
        tid=str(r.ID_TAREA)
        settings[tid]['event']=st.text_input(str(r.NOMBRE_TAREA),
            value=settings[tid]['event'],key='evt_'+tid).strip()

with st.expander('📋 Listado de eventos a preparar en Fracttal'):
    event_names=sorted({settings[str(rr.ID_TAREA)]['event'] for _,rr in ts.iterrows() if rr.ACTIVADOR=='Evento' and settings[str(rr.ID_TAREA)]['event']})
    st.write('Si alguno no existe, créalo desde el catálogo de Fracttal antes de importar esa tarea.')
    st.dataframe(pd.DataFrame({'Evento propuesto':event_names}),hide_index=True,use_container_width=True)
    st.download_button('Descargar listado de eventos',data='\n'.join(event_names),file_name='eventos_fracttal_pendientes.txt',mime='text/plain')

st.header('4 · Seleccionar y comprobar')
ids=list(ts.ID_TAREA)
selected=st.multiselect('Tareas a importar (las existentes se reutilizan)',options=ids,
    default=ids,format_func=lambda i:f'{i} · {ts.loc[ts.ID_TAREA==i,"NOMBRE_TAREA"].iloc[0]}')
if not selected:st.info('Selecciona al menos una tarea.');st.stop()
with st.expander('Ver los datos que se importarán',expanded=False):
    st.dataframe(ts[ts.ID_TAREA.isin(selected)],hide_index=True,use_container_width=True)
    st.dataframe(ss[ss.ID_TAREA.isin(selected)],hide_index=True,use_container_width=True)

# El usuario puede continuar una sesión con una tarea existente que ya tenga sus subtareas.
# El proceso detiene el lote ante incidencias para no generar estados ambiguos.
if st.button('🔎 Comprobar tareas existentes y estado de subtareas (solo lectura)'):
    try:
        access=token();existing_tasks=tasks(access,plan_id)
        diagnostics={}
        for tid in selected:
            rr=ts[ts.ID_TAREA==tid].iloc[0]
            hits=match(existing_tasks,str(rr.NOMBRE_TAREA))
            if len(hits)>1:raise RuntimeError(f'{tid}: hay tareas duplicadas en el plan.')
            subcount=None
            if hits:subcount=len(subtasks(access,int(hits[0]['id'])))
            diagnostics[tid]={'name':str(rr.NOMBRE_TAREA),'id':int(hits[0]['id']) if hits else None,
                              'subcount':subcount,'wanted':len(ss[ss.ID_TAREA==tid])}
        st.session_state.final_preflight={'file':file_hash,'plan':plan_id,'selected':tuple(selected),
            'types':chosen_types.copy(),'settings':json.dumps({i:settings[i] for i in selected},ensure_ascii=False,sort_keys=True),
            'diag':diagnostics}
        st.success('Consulta completada. Revisa el resumen.')
    except Exception as ex:
        st.session_state.pop('final_preflight',None);st.error(fmt_error(ex))
check=st.session_state.get('final_preflight')
valid=bool(check and check['file']==file_hash and check['plan']==plan_id and check['selected']==tuple(selected)
    and check['types']==chosen_types and check['settings']==json.dumps({i:settings[i] for i in selected},ensure_ascii=False,sort_keys=True))
if valid:
    table=[]
    for tid,v in check['diag'].items():
        status='Pendiente' if v['id'] is None else ('Completa' if v['subcount']==v['wanted'] else f'Existente · {v["subcount"]}/{v["wanted"]} subtareas')
        table.append({'Tarea':tid,'Nombre':v['name'],'Estado':status})
    st.dataframe(pd.DataFrame(table),hide_index=True,use_container_width=True)
else:st.info('Ejecuta la comprobación y no cambies selecciones antes de importar.')

st.header('5 · Importar (sin activos)')
st.warning('Fracttal exige que los eventos existan en su catálogo. Si falta alguno, la tarea se detendrá con un error sin modificar los demás registros.')
ack_events=st.checkbox('He comprobado en Fracttal los eventos de las tareas seleccionadas.')
ack_final=st.checkbox('Autorizo crear únicamente las tareas/subtareas pendientes, sin borrar, editar ni vincular activos.')
confirm_text=st.text_input('Escribe IMPORTAR PLAN para iniciar el lote')

unresolved=[i for i in selected if settings[i]['kind']=='Fecha' and settings[i]['unit'] not in frequency_map]
fixed_pending=[]
if unresolved:st.error('No se pueden importar por fecha estas tareas sin conocer la unidad exacta: '+', '.join(unresolved))
if fixed_pending:st.warning('Programación fija pendiente de comprobación en la API para: '+', '.join(fixed_pending))

def build_trigger(i):
    conf=settings[i]
    if conf['kind']=='Evento':
        if not conf['event']:raise RuntimeError(f'{i}: falta evento')
        return {'id_task_trigger_type':4,'event_description':conf['event']}
    unit_id=frequency_map.get(conf['unit'])
    if not unit_id:raise RuntimeError(f'{i}: periodicidad no verificada')
    tr={'id_task_trigger_type':1,'value_main':conf['amount'],'id_period_date':unit_id,
        'id_replay':1 if conf['replay']=='Siempre' else 2}
    if conf['repetitions'] is not None:tr['value_end']=int(conf['repetitions'])
    return tr

def build_subs(tid):
    rows=ss[ss.ID_TAREA==tid].copy()
    rows['_ord']=rows.ORDEN.astype(int)
    rows=rows.sort_values('_ord')
    result=[]
    for _,row in rows.iterrows():
        sub={'description':str(row.DESCRIPCION).strip(),'id_task_form_item_type':TYPES[row.TIPO_SUBTAREA],
            'is_required':norm(row.OBLIGATORIA)=='si',
            'attachments_required':norm(row.ADJUNTO_OBLIGATORIO)=='si',
            'task_form_item_group_description':str(row.GRUPO_PARTE).strip()}
        if row.TIPO_SUBTAREA=='Lista':
            sub['dropdown_options']=[{'description':v.strip()} for v in str(row.get('OPCIONES_RESPUESTA','')).split('|') if v.strip()]
        result.append(sub)
    return result

allowed=bool(valid and not unresolved and ack_events and ack_final and confirm_text=='IMPORTAR PLAN')
run_key='completed_final_'+str(plan_id)+'_'+file_hash+'_'+hashlib.sha256(repr(tuple(selected)).encode()).hexdigest()[:12]
if st.session_state.get(run_key):st.info('Se ejecutó este lote durante la sesión. Actualiza y vuelve a comprobar el estado antes de otra importación.')
if st.button('🚀 Importar tareas seleccionadas',type='primary',disabled=not allowed or bool(st.session_state.get(run_key))):
    st.session_state[run_key]=True
    logs=[]
    progress=st.progress(0)
    try:
        access=token()
        for n,tid in enumerate(selected,1):
            rr=ts[ts.ID_TAREA==tid].iloc[0]
            name=str(rr.NOMBRE_TAREA).strip()
            expected=len(ss[ss.ID_TAREA==tid])
            current=match(tasks(access,plan_id),name)
            if len(current)>1:raise RuntimeError(f'{tid}: tarea duplicada; detenido.')
            if not current:
                tr=build_trigger(tid)
                duration=max(60,60*int(rr.ESTIMACION_MIN or 10))
                payload={'description':name,'id_task_type_main':int(chosen_types[rr.TIPO_INICIAL][1]),
                         'id_group_task':int(plan_id),'id_priorities':3,'duration':duration,
                         'stop_assets_sec':0,'triggers':[tr],'subtasks':[],'resources':[]}
                _,response=api('POST','/tasks_plan/',access,payload)
                new_id=extract_id(response)
                if not new_id:
                    logs.append(f'{tid}: respuesta de alta sin ID confirmado. Revisa Fracttal.')
                    raise RuntimeError('Se detuvo el lote ante una respuesta ambigua.')
                current=match(tasks(access,plan_id),name)
                if len(current)!=1 or int(current[0]['id'])!=new_id:
                    raise RuntimeError(f'{tid}: alta no verificable. Revisar plan antes de repetir.')
                logs.append(f'{tid}: tarea creada (ID {new_id}).')
            task_id=int(current[0]['id'])
            old=subtasks(access,task_id)
            if old:
                expected_descriptions=[norm(x['description']) for x in build_subs(tid)]
                actual_descriptions=[norm(x.get('description','')) for x in old]
                if len(old)==expected and sorted(actual_descriptions)==sorted(expected_descriptions):
                    logs.append(f'{tid}: subtareas presentes y descripciones coincidentes; omitida.')
                else:
                    logs.append(f'{tid}: subtareas existentes distintas del Excel ({len(old)}/{expected}). Se detiene para no duplicar.')
                    raise RuntimeError(f'{tid}: subtareas parciales; requiere revisión manual.')
            elif expected:
                payload_sub={'id_task':task_id,'is_cyclical':False,'subtasks':build_subs(tid)}
                api('POST','/tasks_subtasks/',access,payload_sub)
                after=subtasks(access,task_id)
                if len(after)!=expected or sorted(norm(x.get('description','')) for x in after)!=sorted(norm(x['description']) for x in build_subs(tid)):
                    logs.append(f'{tid}: se esperaban {expected}, Fracttal devuelve {len(after)}.')
                    raise RuntimeError('Se detuvo el lote: número de subtareas no confirmado.')
                logs.append(f'{tid}: creadas {expected} subtareas.')
            progress.progress(n/len(selected))
        st.success('Lote finalizado y comprobado. Vincula los activos manualmente en Fracttal.')
    except Exception as ex:
        st.error(fmt_error(ex))
        st.warning('Importación interrumpida. Los registros anteriores pueden haberse creado: compruébalos ANTES de ejecutar otro lote.')
    st.session_state.final_logs=logs
if st.session_state.get('final_logs'):
    st.subheader('Registro de la última operación')
    for msg in st.session_state.final_logs:st.write(msg)
    st.download_button('Descargar registro',data='\n'.join(st.session_state.final_logs),file_name='importacion_fracttal_resumen.txt',mime='text/plain')
st.caption('Los tipos del Excel se asignan en bloque. Las periodicidades son opciones del activador Fecha, no unidades de lectura. No se vinculan activos ni se crean OT.')
