import React,{useEffect,useState} from 'react';
import {api,record} from './api';
function decodeVapidKey(value){
 const raw=atob((value+'='.repeat((4-value.length%4)%4)).replace(/-/g,'+').replace(/_/g,'/'));
 return Uint8Array.from(raw,c=>c.charCodeAt(0));
}
export default function PushControls({user}){
 const [config,setConfig]=useState(null),[enabled,setEnabled]=useState(false),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const mechanic=user.role==='MECHANIC'||user.organizations?.some(o=>o.role==='MECHANIC');
 const available=window.isSecureContext&&'serviceWorker'in navigator&&'PushManager'in window&&'Notification'in window;
 const denied=available&&Notification.permission==='denied';
 useEffect(()=>{
   if(!mechanic)return;
   api('push/public-key/').then(async next=>{
     setConfig(next);
     if(!available)return;
     const reg=await navigator.serviceWorker.getRegistration('/');
     const sub=await reg?.pushManager.getSubscription();
     // Re-link the existing browser subscription to the current signed-in person.
     if(sub&&next.enabled&&Notification.permission==='granted'){
       await api('push/subscription/',{method:'POST',body:sub.toJSON()});setEnabled(true);
     }else setEnabled(false);
   }).catch(e=>{setEnabled(false);setMessage(e.message||'Не удалось проверить push.');});
 },[user.id,user.organization_id,mechanic]);
 if(!mechanic)return null;
 const readyWorker=async()=>{
   const permission=await Notification.requestPermission();
   record('notification','permission',permission);
   if(permission!=='granted')throw new Error('Разрешите уведомления для этого сайта в настройках браузера и телефона.');
   await navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'});
   return navigator.serviceWorker.ready;
 };
 const run=async fn=>{if(busy)return;setBusy(true);setMessage('');try{await fn();}catch(e){record('notification','setup',e.name||'error');setMessage(e.message||'Не удалось подключить уведомления.');}finally{setBusy(false);}};
 const enable=()=>run(async()=>{
   const ready=await readyWorker();let sub=await ready.pushManager.getSubscription();
   if(!sub)sub=await ready.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:decodeVapidKey(config.public_key)});
   await api('push/subscription/',{method:'POST',body:sub.toJSON()});setEnabled(true);
   setMessage('Системные уведомления подключены. Они приходят по всем вашим организациям, где у вас есть доступ механика.');
 });
 const disable=()=>run(async()=>{
   const reg=await navigator.serviceWorker.getRegistration('/');const sub=await reg?.pushManager.getSubscription();
   if(sub){await api('push/subscription/',{method:'DELETE',body:{endpoint:sub.endpoint}});await sub.unsubscribe();}
   setEnabled(false);setMessage('Уведомления отключены на этом устройстве.');
 });
 const test=()=>run(async()=>{
   const ready=await readyWorker();
   await ready.showNotification('Лифт Контроль — проверка',{
     body:'Проверка системного уведомления и настроек звука телефона.',
     tag:'liftcontrol-test',silent:false,renotify:true,vibrate:[250,100,250],data:{url:'/#tickets'},
   });
   setMessage('Отправлено системное уведомление. Если звука нет, проверьте звук уведомлений браузера в Android и режим «Не беспокоить». Эта кнопка проверяет телефон; доставку с сервера проверяют новой заявкой.');
 });
 return <div className="setting-row"><div><h2>Системные уведомления</h2><p>Новая заявка, принятие и завершение — в уведомлениях телефона, включая фон приложения.</p>
 <p>{!available?'Браузер не поддерживает push по этому адресу. Откройте поддерживаемый безопасный адрес.':denied?'Разрешение заблокировано. Разрешите уведомления для сайта в настройках браузера.':!config?.enabled?'Доставка с сервера пока не подключена. Сообщения внутри приложения продолжают работать.':enabled?'Подключено на этом устройстве.':'Для этого телефона уведомления ещё не включены.'}</p>
 <small>Системным звуком управляет телефон. Для открытия заявок через SSH нужен активный туннель; для постоянной работы используйте доступный адрес системы.</small>
 {message&&<p role="status">{message}</p>}<div className="actions"><button type="button" disabled={busy||!available||(!enabled&&!config?.enabled)} onClick={enabled?disable:enable}>{busy?'Обработка…':enabled?'Отключить push':'Включить push'}</button><button type="button" disabled={busy||!available||denied} onClick={test}>Проверить системное уведомление</button></div></div></div>;
}
