import React,{useCallback,useEffect,useState} from 'react';
import {api} from './api';

/** Durable per-organization inbox. Reading never accepts a ticket or task. */
export default function Notifications({user,onOpen=()=>{}}) {
  const [items,setItems]=useState([]),[unread,setUnread]=useState(0);
  const [onlyNew,setOnlyNew]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);

  const load=useCallback(async()=>{
    try{
      const data=await api('notifications/?unread='+(onlyNew?'1':'0')+'&limit=100');
      setItems(data.results||[]);setUnread(data.unread||0);setError('');
    }catch(e){setError(e.message);}
  },[onlyNew,user.organization_id]);

  useEffect(()=>{load();},[load]);

  async function open(item) {
    if(busy)return;
    setBusy(true);
    try{
      if(!item.read_at)await api('notifications/'+item.id+'/read/',{method:'POST',body:{}});
      await load();
      onOpen(item.target_type,item.target_id);
    }catch(e){setError(e.message);}
    finally{setBusy(false);}
  }
  return <>
    <div className="page-heading"><div>
      <span className="eyebrow">ЦЕНТР УВЕДОМЛЕНИЙ</span>
      <h1>Все уведомления</h1>
      <p>Непрочитанных: {unread}. Прочтение не означает принятие заявки.</p>
    </div>
    <div className="actions"><label><input type="checkbox" checked={onlyNew}
      onChange={e=>setOnlyNew(e.target.checked)}/> Только непрочитанные</label>
      <button disabled={busy} onClick={load}>Обновить</button></div></div>
    {error&&<p className="error" role="alert">{error}</p>}
    <section className="panel">
      {items.map(item=><button type="button" className="list-row"
        disabled={busy} key={item.id} onClick={()=>open(item)}>
        <span style={{flex:1,textAlign:'left'}}>
          <b>{item.read_at?'':'● '}{item.title}</b>
          <small>{new Date(item.created_at).toLocaleString('ru-RU')}</small>
        </span><span>{item.read_at?'Прочитано':'Новое'}</span>
      </button>)}
      {!items.length&&<p className="empty">Новых записей нет.</p>}
    </section>
  </>;
}
