import React,{useEffect,useState} from 'react';
import {api,PREVIEW} from './api';

const categories=[['entrapment','Застревание','Люди в кабине. Отдельный срочный сигнал в Android.'],
 ['urgent','Высокие и аварийные заявки','Отдельная категория срочных обращений.'],
 ['ordinary','Обычные заявки','Обычный и низкий приоритет.'],
 ['updates','Изменения статусов','Принятие, остановка, продолжение и завершение.'],
 ['tasks','Личные задания','Назначение вам задания и результат выданного вами задания.']];

export default function NotificationPreferences({user}){
 const [values,setValues]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;if(PREVIEW){setValues(Object.fromEntries(categories.map(([k])=>[k,true])));return;}
  api('notification-preferences/').then(v=>{if(live)setValues(v);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[user.organization_id]);
 const change=async(key,value)=>{if(busy)return;setBusy(true);setError('');try{
  setValues(PREVIEW?{...values,[key]:value}:await api('notification-preferences/',{method:'PATCH',body:{[key]:value}}));
 }catch(e){setError(e.message);}finally{setBusy(false);}};
 return <div className="setting-row notification-preferences"><div><h2>Категории уведомлений</h2><p>{user.organization}. Выбор действует на всех ваших устройствах в этой организации.</p>
  {values&&categories.map(([key,title,description])=><label className="notification-choice" key={key}><input type="checkbox" role="switch" aria-label={title} disabled={busy} checked={values[key]===true} onChange={e=>change(key,e.target.checked)}/><span><b>{title}</b><small>{description}</small></span></label>)}
  <p className="muted small">Звук и вибрация каждой категории настраиваются в Android-приложении. Браузер использует настройки своих уведомлений.</p>{error&&<p className="error" role="alert">{error}</p>}</div></div>;
}
