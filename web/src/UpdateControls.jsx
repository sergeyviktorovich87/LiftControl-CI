import React,{useEffect,useState} from 'react';
import {api} from './api';

export default function UpdateControls({user}){
 const [release,setRelease]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const check=async()=>{setBusy(true);setError('');try{setRelease(await api('updates/'));}catch(e){setError(e.message);}finally{setBusy(false);}};
 useEffect(()=>{check();},[]);
 return <div className="setting-row"><div><h2>Обновления</h2>
  <p>Панель и сервер: {release?.installed?.panel||'не определена'}</p>
  {release?.android&&<p>Android: {release.android.version} · {release.android.notes}</p>}
  {user.can_manage_access&&<p>{release?.panel?'Подготовлен серверный пакет '+release.panel.version+'. '+release.panel.notes:'Новых серверных пакетов пока нет.'}</p>}
  {user.can_manage_access&&<small>Обновление сервера устанавливает администратор командой из пакета. Перед установкой сохраняется копия базы.</small>}
  {error&&<p className="error" role="alert">{error}</p>}
  <div className="actions"><button disabled={busy} onClick={check}>{busy?'Проверка…':'Проверить обновления'}</button>
  {release?.android&&<a className="button" href="/api/updates/android/download/">Скачать Android</a>}
  {release?.panel&&<a className="button" href="/api/updates/panel/download/">Скачать пакет сервера</a>}</div>
 </div></div>;
}
