import { demoRequest } from './demo.js';
export const PREVIEW = import.meta.env.VITE_PREVIEW === 'true';
let csrf = '';
let selectedOrganization = null;
try {
 const query = new URLSearchParams(location.search).get('organization');
 const saved = query || sessionStorage.getItem('liftcontrol-organization');
 if (/^[1-9][0-9]*$/.test(saved || '')) selectedOrganization = Number(saved);
} catch {}
export function selectOrganization(id) {
 selectedOrganization = Number(id);
 try { sessionStorage.setItem('liftcontrol-organization',String(id)); } catch {}
}
export const diagnosticEvents = [];
export function record(kind, path, code = '') {
  diagnosticEvents.push({time:new Date().toISOString(),kind,path:path.split('?')[0],code:String(code)});
  if(diagnosticEvents.length>200) diagnosticEvents.shift();
}
export async function api(path, options = {}) {
  const method = options.method || 'GET';
  if(PREVIEW) {
    try { const value=await demoRequest(path,options);record(method,path,200);return value; }
    catch(error) { record(method,path,'error');throw error; }
  }
  const headers = {'Accept':'application/json'};
  if(selectedOrganization) headers['X-Organization-ID']=String(selectedOrganization);
  let body=options.body;
  if(body && !(body instanceof FormData)) {headers['Content-Type']='application/json';body=JSON.stringify(body);}
  if(method!=='GET')headers['X-CSRFToken']=csrf;
  try {
    const response=await fetch('/api/'+path,{method,headers,body,credentials:'same-origin',cache:'no-store',signal:options.signal});
    record(method,path,response.status);
    if(!response.ok) {
      let detail;try{detail=await response.json();}catch{detail={detail:'Ошибка сервера. Повторите запрос.'};}
      const error=new Error(response.status===401?'Войдите в систему.':Object.values(detail).flat().join(' '));error.status=response.status;throw error;
    }
    if(options.blob)return response.blob();
    const data=response.status===204?null:await response.json();
    if(path==='me/') {csrf=data.csrf; if(data.organization_id)selectOrganization(data.organization_id);}
    if(path==='organizations/' && method==='GET') csrf=data.csrf;
    return data;
  }catch(error){record('error',path,error.status||'network');throw error;}
}
export function download(name,content,type='application/json') {
  const url=URL.createObjectURL(content instanceof Blob?content:new Blob([content],{type}));
  const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function csv(name,rows) {
  const safe=x=>{let s=String(x??'');if(/^[\s]*[=+@-]/.test(s)||/^[\t\r\n]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
  download(name,'\ufeff'+rows.map(r=>r.map(safe).join(',')).join('\r\n'),'text/csv;charset=utf-8');
}

