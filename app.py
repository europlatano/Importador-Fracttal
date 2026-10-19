"""Europlátano · V19 | eventos editables y alta previa al lote.
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
st.caption('Europlátano · Importador Universal · varios planes · incidencias manuales')
st.info('Un solo Excel para uno o varios planes. Las frecuencias no verificadas se notifican y se configuran manualmente.')

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

def infer_date_label(text):
    """Interpreta nombres y descripciones de calendario. No interpreta unidades de lectura."""
    x=norm(text)
    if not x:return None
    pieces=re.split(r'[^a-z]+',x)
    groups={'Día':('day','days','dia','dias','daily'),
            'Semana':('week','weeks','semana','semanas','weekly'),
            'Mes':('month','months','mes','meses','monthly'),
            'Año':('year','years','ano','anos','yearly','annual')}
    hits=[name for name,variants in groups.items() if any(v in pieces for v in variants)]
    return hits[0] if len(hits)==1 else None

def discover_freq(t):
    records=[]
    # Conservamos valor y descripción de la MISMA fila, nunca mezclamos catálogos.
    for path in ('/tasks_triggers/','/tasks/'):
        try:
            dataset=paginated(t,path)
        except Exception:
            continue
        for r in dataset:
            if not isinstance(r,dict):continue
            samples=[r]
            if isinstance(r.get('triggers'),list):
                samples += [z for z in r['triggers'] if isinstance(z,dict)]
            for record in samples:
                pid=positive_int(record.get('id_period_date'))
                if not pid:continue
                try:kind=int(record.get('id_task_trigger_type',1))
                except (ValueError,TypeError):kind=1
                if kind!=1:continue
                keys=('period_dates_description','period_date_description','period_description',
                      'period_date','period_dates','frequency_description','trigger_description',
                      'description','date_frequency_description')
                seen=set()
                for key in keys:
                    val=record.get(key)
                    if not isinstance(val,str):continue
                    unit=infer_date_label(val)
                    if unit and unit not in seen:
                        records.append({'Unidad':unit,'ID':pid,'Fuente':path,'Campo':key})
                        seen.add(unit)
    mapping={}
    conflicts=[]
    for name in ('Día','Semana','Mes','Año'):
        ids={r['ID'] for r in records if r['Unidad']==name}
        if len(ids)==1:mapping[name]=next(iter(ids))
        elif len(ids)>1:conflicts.append(name)
    # Fuente adicional independiente: el ejemplo OFICIAL de POST /tasks_plan/
    # documenta explícitamente un mantenimiento MENSUAL con id_period_date=2.
    if 'Mes' not in mapping and 'Mes' not in conflicts:mapping['Mes']=2
    return records,mapping,conflicts

# Fracttal publica la consulta/uso de eventos existentes, pero no se ha verificado
# públicamente un POST del catálogo. Solo se usará un endpoint aprobado expresamente.
def confirmed_event_endpoint():
    path=secret('FRACTTAL_EVENT_CREATE_PATH')
    if not path:return None
    # Impide endpoints arbitrarios, hosts externos y modificación de otras áreas.
    if not re.fullmatch(r'/[a-z][a-z0-9_/-]*/',path):
        raise RuntimeError('FRACTTAL_EVENT_CREATE_PATH no tiene un formato permitido.')
    if not any(x in path.lower() for x in ('event','evento')):
        raise RuntimeError('El endpoint configurado no parece pertenecer a eventos.')
    if path in ('/tasks_scheduled/','/tasks_nonscheduled/'):
        raise RuntimeError('No se pueden crear eventos lanzando OTs.')
    return path

def existing_events_from_triggers(t):
    try:data=paginated(t,'/tasks_triggers/')
    except Exception:return set(),False
    names=set()
    for r in data:
        if not isinstance(r,dict):continue
        values=[r.get(k) for k in ('events_description','event_description','event_name','description')]
        desc=r.get('trigger_description')
        if isinstance(desc,str) and desc.upper().startswith('EVENT$'):
            values.append(desc.split('$',1)[1])
        for v in values:
            if isinstance(v,str) and v.strip() and str(r.get('id_task_trigger_type','4'))=='4':
                names.add(norm(v))
    # Son eventos utilizados en tareas, NO necesariamente todo el catálogo.
    return names,True

def create_event_confirmed(t,name,path):
    # Solo se permite cuando la empresa confirma el contrato de su endpoint.
    field=secret('FRACTTAL_EVENT_CREATE_FIELD') or 'description'
    if field not in ('description','event_description'):
        raise RuntimeError('Nombre de campo de alta de evento no verificado.')
    _,answer=api('POST',path,t,{field:name})
    if not isinstance(answer,dict) or answer.get('success') is not True:
        raise RuntimeError('El catálogo no confirmó el alta del evento. Se detiene el lote.')
    return answer

def extract_id(out):
    data=out.get('data') if isinstance(out,dict) else None
    if isinstance(data,dict):return positive_int(data.get('id'))
    return None

def required_event_name(name,task_name):
    return (name or task_name).strip()



st.header('1 · Plantilla universal')
file=st.file_uploader('Selecciona el Excel cumplimentado', type=['xlsx'])
if file is None:
    st.info('El mismo Excel sirve para una máquina individual o para varios planes de un conjunto de máquinas.')
    st.stop()
blob=file.getvalue()
fingerprint=hashlib.sha256(blob).hexdigest()
try:
    wb=pd.read_excel(io.BytesIO(blob),sheet_name=None,dtype=str,keep_default_na=False)
except Exception as exc:
    st.error('No se puede leer el Excel: '+str(exc));st.stop()
required={'PLANES':['ID_PLAN','NOMBRE_PLAN'],
          'TAREAS':['ID_TAREA','ID_PLAN','NOMBRE_TAREA','TIPO_INICIAL','ACTIVADOR'],
          'SUBTAREAS':['ID_TAREA','ORDEN','TIPO_SUBTAREA','GRUPO_PARTE','DESCRIPCION','OBLIGATORIA','ADJUNTO_OBLIGATORIO']}
errors=[]
for sheet,cols in required.items():
    if sheet not in wb:errors.append('Falta la hoja '+sheet)
    else:
        errors.extend(f'Falta la columna {sheet}.{c}' for c in cols if c not in wb[sheet].columns)
if errors:
    st.error('\n'.join(errors));st.stop()
ps,ts,ss=[wb[k].fillna('').copy() for k in ('PLANES','TAREAS','SUBTAREAS')]
# Filas de plantilla vacías (el libro trae rangos preformateados).
for df,key in [(ps,'ID_PLAN'),(ts,'ID_TAREA'),(ss,'ID_TAREA')]:
    df.drop(df[df[key].astype(str).str.strip()==''].index,inplace=True)
for df in [ps,ts,ss]:
    for col in df.columns:df[col]=df[col].astype(str).str.strip()
for col,default in [('INTERVALO',''),('UNIDAD_INTERVALO',''),('ESTIMACION_MIN','10'),('TIEMPO_PARO_MIN','0'),
    ('PRIORIDAD','Media'),('REPETIR','Siempre'),('NUMERO_REPETICIONES',''),('PROGRAMACION_FIJA',''),
    ('NOMBRE_EVENTO',''),('HABILITADA','Sí')]:
    if col not in ts.columns:ts[col]=default
if 'OPCIONES_RESPUESTA' not in ss.columns:ss['OPCIONES_RESPUESTA']=''
if not len(ps) or not len(ts):errors.append('Hay que cumplimentar PLANES y TAREAS.')
if ps.ID_PLAN.duplicated().any():errors.append('ID_PLAN repetido.')
if ts.ID_TAREA.duplicated().any():errors.append('ID_TAREA repetido (debe ser único en todo el libro).')
if set(ts.ID_PLAN)-set(ps.ID_PLAN):errors.append('Hay tareas que no corresponden a ningún ID_PLAN.')
if set(ss.ID_TAREA)-set(ts.ID_TAREA):errors.append('Hay subtareas sin ID_TAREA válido.')
if ss[['ID_TAREA','ORDEN']].duplicated().any():errors.append('Orden duplicado dentro de una tarea.')
if ps.NOMBRE_PLAN.str.casefold().duplicated().any():errors.append('Dos planes tienen el mismo nombre.')
for _,r in ts.iterrows():
    if not str(r.NOMBRE_TAREA):errors.append(f'{r.ID_TAREA}: falta NOMBRE_TAREA')
    if not str(r.TIPO_INICIAL):errors.append(f'{r.ID_TAREA}: falta TIPO_INICIAL')
    try:
        if int(r.ESTIMACION_MIN)<1 or int(r.TIEMPO_PARO_MIN)<0:raise ValueError
    except (ValueError,TypeError):errors.append(f'{r.ID_TAREA}: ESTIMACION_MIN/TIEMPO_PARO_MIN deben ser enteros válidos')
for _,r in ss.iterrows():
    if r.TIPO_SUBTAREA not in TYPES:errors.append(f'{r.ID_TAREA}-{r.ORDEN}: tipo de subtarea desconocido')
    if not r.GRUPO_PARTE or not r.DESCRIPCION:errors.append(f'{r.ID_TAREA}-{r.ORDEN}: faltan Grupo/Parte o descripción')
    if norm(r.OBLIGATORIA) not in ('si','no') or norm(r.ADJUNTO_OBLIGATORIO) not in ('si','no'):
        errors.append(f'{r.ID_TAREA}-{r.ORDEN}: utiliza Sí o No en obligatoriedad')
    if r.TIPO_SUBTAREA=='Lista' and len([x for x in r.OPCIONES_RESPUESTA.split('|') if x.strip()])<2:
        errors.append(f'{r.ID_TAREA}-{r.ORDEN}: una Lista necesita opciones separadas por |')
    try:
        if int(r.ORDEN)<1:raise ValueError
    except (ValueError,TypeError):errors.append(f'{r.ID_TAREA}: orden de subtarea inválido')
# La última observación es opcional por defecto para cada tarea.
OBS='OBSERVACIONES ADICIONALES (si procede)'
extra=[]
for _,row in ts.iterrows():
    children=ss[ss.ID_TAREA==row.ID_TAREA]
    if any(norm(x).startswith('observaciones adicionales') or norm(x)=='observaciones' for x in children.DESCRIPCION):continue
    try:order=max([int(x) for x in children.ORDEN]+[0])+1
    except ValueError:order=1
    extra.append({'ID_TAREA':row.ID_TAREA,'ORDEN':str(order),'TIPO_SUBTAREA':'Texto',
      'GRUPO_PARTE':'OBSERVACIONES GENERALES','DESCRIPCION':OBS,'OPCIONES_RESPUESTA':'',
      'OBLIGATORIA':'No','ADJUNTO_OBLIGATORIO':'No'})
if extra:ss=pd.concat([ss,pd.DataFrame(extra)],ignore_index=True).fillna('')
cols=st.columns(4)
for c,(label,value) in zip(cols,[('Planes',len(ps)),('Tareas',len(ts)),('Subtareas',len(ss)),('Errores',len(errors))]):c.metric(label,value)
if errors:
    st.error('Corrige estos problemas:')
    for issue in errors:st.write('• '+issue)
    st.stop()
st.success('Excel validado. Se añadirán automáticamente observaciones opcionales cuando falten.')

st.header('2 · Consultar Fracttal')
if not ready:st.error('Faltan credenciales en los Secrets de Streamlit.');st.stop()
if st.button('Actualizar catálogos y planes (solo lectura)',type='primary'):
    try:
        bearer=token()
        all_plans=plans(bearer)
        known_tasks=paginated(bearer,'/tasks/')
        options=sorted({(str(x.get('tasks_types_main_description','')).strip(),int(x['id_task_type_main']))
            for x in known_tasks if str(x.get('id_task_type_main','')).isdigit() and x.get('tasks_types_main_description')},key=lambda z:(norm(z[0]),z[1]))
        ref,map_freq,conflicts=discover_freq(bearer)
        st.session_state.univ_catalog={'plans':all_plans,'types':options,'periods':map_freq,
          'conflicts':conflicts,'refs':ref,'fingerprint':fingerprint}
        st.success('Catálogos consultados. No se ha modificado Fracttal.')
    except Exception as ex:st.error(fmt_error(ex))
cat=st.session_state.get('univ_catalog')
if not cat or cat['fingerprint']!=fingerprint:
    st.info('Pulsa el botón de actualización para consultar Fracttal.');st.stop()

# Los tipos se resuelven por nombre; se pide intervención solo para coincidencias ambiguas.
def type_choice(value,existing):
    wanted=norm(value)
    matches=[x for x in existing if norm(x[0])==wanted]
    if len(matches)==1:return matches[0]
    family=next((x for x in ('preventivo','correctivo','predictivo','mejora') if x in wanted),None)
    if family:
        scope='externo' if 'extern' in wanted else 'interno'
        matches=[x for x in existing if family in norm(x[0]) and scope in norm(x[0])]
        if len(matches)==1:return matches[0]
    return None
mappings={x:type_choice(x,cat['types']) for x in sorted(set(ts.TIPO_INICIAL))}
with st.expander('Correspondencias de tipos (solo si hay alguna sin identificar)'):
    for label in mappings:
        idx=cat['types'].index(mappings[label])+1 if mappings[label] in cat['types'] else 0
        mappings[label]=st.selectbox(label,[None]+cat['types'],index=idx,
           format_func=lambda v:'Sin resolver' if v is None else f'{v[0]} (ID {v[1]})',key='type_'+label)
if any(v is None for v in mappings.values()):st.warning('Hay tipos sin identificar. Esas tareas se dejarán pendientes.')

st.header('3 · Selección y revisión global')
plan_map={r.ID_PLAN:r.NOMBRE_PLAN for _,r in ps.iterrows()}
choice=st.multiselect('Planes que deseas procesar',list(plan_map),default=list(plan_map),
     format_func=lambda key:plan_map[key])
selected=ts[ts.ID_PLAN.isin(choice)]
# La aplicación nunca decide códigos sin evidencia. El usuario acepta configurarlos después.
periods=cat['periods']
if cat['conflicts']:st.warning('Se han encontrado referencias de periodicidad contradictorias: '+', '.join(cat['conflicts']))
events={}
for _,r in selected.iterrows():
    if norm(r.ACTIVADOR)=='evento':
        suggested=r.NOMBRE_EVENTO or r.NOMBRE_TAREA
        events[r.ID_TAREA]=suggested
with st.expander('Nombres de eventos (puedes modificarlos; no se crean automáticamente sin endpoint verificado)'):
    for _,r in selected.iterrows():
        if r.ID_TAREA in events:
            events[r.ID_TAREA]=st.text_input(f'{r.ID_TAREA} · {r.NOMBRE_TAREA}',value=events[r.ID_TAREA],key='ev_'+r.ID_TAREA).strip()


confirmed_events={}
with st.expander('Marcar eventos ya existentes en Fracttal (no se crean desde esta versión)'):
    st.caption('Confirma únicamente los nombres que hayas comprobado en el catálogo de Fracttal. Las tareas con evento sin confirmar quedarán pendientes y NO bloquearán otras tareas.')
    for _,r in selected.iterrows():
        if r.ID_TAREA in events:
            confirmed_events[r.ID_TAREA]=st.checkbox(f'Existe: {r.ID_TAREA} · {events[r.ID_TAREA]}',value=False,key='exists_'+r.ID_TAREA+'_'+hashlib.sha256(events[r.ID_TAREA].encode()).hexdigest()[:8])

def task_status(r):
    if mappings.get(r.TIPO_INICIAL) is None:return 'MANUAL · Tipo sin identificar'
    if norm(r.PRIORIDAD) not in ('','media'):return 'MANUAL · Prioridad sin ID verificado'
    kind=norm(r.ACTIVADOR)
    if kind=='evento':
        if not events.get(r.ID_TAREA):return 'MANUAL · Evento sin nombre'
        if not confirmed_events.get(r.ID_TAREA):return 'MANUAL · Evento no confirmado en catálogo'
        return 'LISTA · Evento confirmado'
    if kind!='fecha':return 'MANUAL · Activador no integrado'
    unit=unit_name(r.UNIDAD_INTERVALO)
    if not unit:return 'MANUAL · Unidad o día concreto sin conversión API'
    if unit not in periods or unit in cat['conflicts']:return 'MANUAL · '+unit+' sin identificador comprobado'
    try:
        if int(r.INTERVALO)<1:raise ValueError
    except ValueError:return 'MANUAL · Intervalo no válido'
    if norm(r.REPETIR or 'Siempre')!='siempre':return 'MANUAL · Repetición limitada pendiente de verificar'
    if norm(r.PROGRAMACION_FIJA) in ('si','no'):return 'MANUAL · Programación fija pendiente de API'
    return 'LISTA'
preview=[]
for _,r in selected.iterrows():
    preview.append({'Plan':plan_map[r.ID_PLAN],'ID_TAREA':r.ID_TAREA,
        'Tarea':r.NOMBRE_TAREA,'Activador':r.ACTIVADOR,
        'Hacer cada':r.INTERVALO if norm(r.ACTIVADOR)=='fecha' else '',
        'Frecuencia':r.UNIDAD_INTERVALO if norm(r.ACTIVADOR)=='fecha' else '',
        'Estado':task_status(r)})
pre=pd.DataFrame(preview)
st.dataframe(pre,hide_index=True,use_container_width=True)
blocked=pre[pre.Estado.str.startswith('MANUAL')]
if len(blocked):
    st.warning(f'{len(blocked)} tarea(s) necesitan configuración manual. No impedirán importar las demás.')
    st.download_button('Descargar tareas pendientes de configuración',blocked.to_csv(index=False).encode('utf-8-sig'),
        'tareas_configuracion_manual.csv','text/csv')

st.header('4 · Verificar existentes y autorizar')
if st.button('Comprobar planes, tareas y subtareas existentes (solo lectura)'):
    try:
        bearer=token();live_plans=plans(bearer);diagnostics={};issues=[]
        for plan_key in choice:
            plan_title=plan_map[plan_key]
            hits=match(live_plans,plan_title)
            if len(hits)>1:raise RuntimeError('Plan duplicado en Fracttal: '+plan_title)
            pid=positive_int(hits[0].get('id')) if hits else None
            p_tasks=tasks(bearer,pid) if pid else []
            diagnostics[plan_key]={'name':plan_title,'id':pid,'tasks':{}}
            for _,r in selected[selected.ID_PLAN==plan_key].iterrows():
                match_t=match(p_tasks,r.NOMBRE_TAREA)
                if len(match_t)>1:raise RuntimeError('Tarea duplicada: '+r.NOMBRE_TAREA)
                tid=positive_int(match_t[0].get('id')) if match_t else None
                old=subtasks(bearer,tid) if tid else []
                diagnostics[plan_key]['tasks'][r.ID_TAREA]={'id':tid,'subtitles':[norm(x.get('description','')) for x in old]}
        st.session_state.univ_check={'fingerprint':fingerprint,'plans':tuple(choice),
               'event_names':dict(events),'confirmed_events':dict(confirmed_events),'diagnostics':diagnostics,'mapping':dict(mappings)}
        st.success('Comprobación completada. Los registros existentes se reutilizarán sin duplicarlos.')
    except Exception as ex:
        st.session_state.pop('univ_check',None);st.error(fmt_error(ex))
check=st.session_state.get('univ_check')
valid=bool(check and check['fingerprint']==fingerprint and check['plans']==tuple(choice) and
   check['event_names']==events and check['confirmed_events']==confirmed_events and check['mapping']==mappings)
if valid:
    stats=[]
    for key,p in check['diagnostics'].items():
        stats.append({'Plan':p['name'],'Estado':'Existe' if p['id'] else 'Por crear',
        'Tareas ya existentes':sum(bool(v['id']) for v in p['tasks'].values())})
    st.dataframe(pd.DataFrame(stats),hide_index=True,use_container_width=True)
else:st.info('Comprueba los existentes después de cualquier cambio en la selección o en los eventos.')
ack=st.checkbox('Autorizo crear los planes y tareas compatibles, sin borrar ni vincular activos.')
code=st.text_input('Escribe IMPORTAR para confirmar')

# Construcción de las solicitudes como en las versiones probadas.
def make_subs(key):
    children=ss[ss.ID_TAREA==key].copy()
    children['_order']=children.ORDEN.astype(int)
    result=[]
    for _,r in children.sort_values('_order').iterrows():
        item={'description':r.DESCRIPCION,'id_task_form_item_type':TYPES[r.TIPO_SUBTAREA],
              'is_required':norm(r.OBLIGATORIA)=='si',
              'attachments_required':norm(r.ADJUNTO_OBLIGATORIO)=='si',
              'task_form_item_group_description':r.GRUPO_PARTE}
        if r.TIPO_SUBTAREA=='Lista':
            item['dropdown_options']=[{'description':val.strip()} for val in r.OPCIONES_RESPUESTA.split('|') if val.strip()]
        result.append(item)
    return result

def make_trigger(r):
    if norm(r.ACTIVADOR)=='evento':return {'id_task_trigger_type':4,'event_description':events[r.ID_TAREA]}
    return {'id_task_trigger_type':1,'value_main':int(r.INTERVALO),
      'id_period_date':int(periods[unit_name(r.UNIDAD_INTERVALO)]),'id_replay':1}

attempt_key='univ_attempt_'+fingerprint+'_'+hashlib.sha256(repr(tuple(choice)).encode()).hexdigest()[:12]
if st.session_state.get(attempt_key):st.warning('Ya se inició esta importación durante la sesión. Verifica el resultado antes de repetirla.')
can_run=bool(valid and choice and ack and code=='IMPORTAR' and not st.session_state.get(attempt_key))
if st.button('Importar planes y tareas compatibles',type='primary',disabled=not can_run):
    st.session_state[attempt_key]=True
    logs=[]
    try:
        bearer=token()
        for plan_key in choice:
            plan_title=plan_map[plan_key]
            live=match(plans(bearer),plan_title)
            if len(live)>1:raise RuntimeError('Plan duplicado: '+plan_title)
            if not live:
                _,out=api('POST','/groups_tasks/',bearer,{'description':plan_title})
                logs.append('Plan: solicitud de alta enviada · '+plan_title)
                live=match(plans(bearer),plan_title)
                if len(live)!=1:raise RuntimeError('Alta de plan no verificable: '+plan_title)
            pid=positive_int(live[0].get('id'))
            if not pid:raise RuntimeError('El plan no devuelve ID: '+plan_title)
            for _,r in selected[selected.ID_PLAN==plan_key].iterrows():
                status=task_status(r)
                if status.startswith('MANUAL'):
                    logs.append('PENDIENTE MANUAL · '+r.ID_TAREA+' · '+status)
                    continue
                found=match(tasks(bearer,pid),r.NOMBRE_TAREA)
                if len(found)>1:raise RuntimeError('Tarea duplicada: '+r.NOMBRE_TAREA)
                if not found:
                    payload={'description':r.NOMBRE_TAREA,
                      'id_task_type_main':int(mappings[r.TIPO_INICIAL][1]),'id_group_task':pid,
                      'id_priorities':3,'duration':int(r.ESTIMACION_MIN)*60,
                      'stop_assets_sec':int(r.TIEMPO_PARO_MIN)*60,'triggers':[make_trigger(r)],
                      'subtasks':[],'resources':[]}
                    api('POST','/tasks_plan/',bearer,payload)
                    logs.append('Tarea: solicitud de alta enviada · '+r.NOMBRE_TAREA)
                    found=match(tasks(bearer,pid),r.NOMBRE_TAREA)
                    if len(found)!=1:raise RuntimeError('Alta de tarea no verificable: '+r.NOMBRE_TAREA)
                tid=positive_int(found[0].get('id'))
                if not tid:raise RuntimeError('La tarea no devuelve ID: '+r.NOMBRE_TAREA)
                existing=subtasks(bearer,tid)
                required_subs=make_subs(r.ID_TAREA)
                old_descriptions=[norm(x.get('description','')) for x in existing]
                new_descriptions=[norm(x['description']) for x in required_subs]
                if sorted(old_descriptions)==sorted(new_descriptions):
                    logs.append('YA EXISTE · '+r.NOMBRE_TAREA);continue
                if existing:
                    logs.append('REVISAR MANUALMENTE · Subtareas diferentes en '+r.NOMBRE_TAREA)
                    continue
                if required_subs:
                    api('POST','/tasks_subtasks/',bearer,
                        {'id_task':tid,'is_cyclical':False,'subtasks':required_subs})
                    after=subtasks(bearer,tid)
                    if sorted(norm(x.get('description','')) for x in after)!=sorted(new_descriptions):
                        raise RuntimeError('No se pudo verificar las subtareas de '+r.NOMBRE_TAREA)
                    logs.append(f'OK · {r.NOMBRE_TAREA}: {len(required_subs)} subtareas verificadas')
        st.success('Procesamiento completado. Revisa las tareas pendientes de configuración manual y vincula activos en Fracttal.')
    except Exception as exc:
        st.error(fmt_error(exc))
        st.warning('Importación detenida. Algunas tareas pueden haberse creado: vuelve a comprobar antes de repetir.')
    finally:
        st.session_state.univ_logs=logs
if st.session_state.get('univ_logs'):
    st.subheader('Informe de importación')
    for line in st.session_state.univ_logs:st.write('• '+line)
    st.download_button('Descargar informe','\n'.join(st.session_state.univ_logs).encode('utf-8'),
       'informe_importacion_fracttal.txt','text/plain')
st.caption('Sin creación de OTs, sin borrados y sin asignación de activos. Los eventos nuevos se crean manualmente mientras Fracttal no confirme el contrato API de su catálogo.')
