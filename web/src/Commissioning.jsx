import React, {useEffect, useState} from 'react';
import {api} from './api';
import {compressImage} from './imageCompression';

/** 0.13 commissioning workspace. All authorization lives on Django, not JSX. */
export default function Commissioning({user}) {
  const [projects,setProjects]=useState([]),[selected,setSelected]=useState(null);
  const [members,setMembers]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [newName,setNewName]=useState(''),[newAddress,setNewAddress]=useState('');
  const [newLift,setNewLift]=useState(''),[factory,setFactory]=useState('');
  const [assignment,setAssignment]=useState({});
  const [progress,setProgress]=useState({}),[description,setDescription]=useState({});
  const [expenseAmount,setExpenseAmount]=useState(''),[expenseText,setExpenseText]=useState('');
  const [expenseLift,setExpenseLift]=useState(''),[receipt,setReceipt]=useState(null);
  const [expenseDate,setExpenseDate]=useState(()=>new Intl.DateTimeFormat('en-CA',{timeZone:user.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()));
  const [expenseOperation,setExpenseOperation]=useState(null);
  const [needText,setNeedText]=useState(''),[needKind,setNeedKind]=useState('OTHER'),[blocking,setBlocking]=useState(false);
  const [extraText,setExtraText]=useState(''),[extraPrice,setExtraPrice]=useState('0');
  const [message,setMessage]=useState(''),[requestLabel,setRequestLabel]=useState('');
  const canManage=user.can_manage_access;
  async function loadList() {
    const data=await api('commissioning/');
    setProjects(data);
    return data;
  }
  async function openProject(id) {setSelected(await api('commissioning/'+id+'/'));}
  useEffect(()=> {
    let alive=true;
    setSelected(null);setProjects([]);setError('');setExpenseOperation(null);
    api('commissioning/').then(rows=>{if(alive)setProjects(rows);}).catch(e=>{if(alive)setError(e.message);});
    if(canManage)api('organizations/members/').then(rows=>{if(alive)setMembers(rows.filter(m=>m.active&&m.can_commissioning&&['ADJUSTER','MECHANIC'].includes(m.role)));}).catch(()=>{});
    return()=>{alive=false;};
  },[user.organization_id,canManage]);
  async function run(action,{refresh=true}={}) {
    if(busy)return;
    setBusy(true);setError('');
    try {
      await action();
      const list=await loadList();
      if(refresh && selected?.id && list.some(p=>p.id===selected.id))await openProject(selected.id);
    }catch(e){setError(e.message || String(e));}
    finally{setBusy(false);}
  }
  async function post(path,body){return api(path,{method:'POST',body});}
  function field(label,value,set,type='text',required=true) {
    return <label className="field">{label}<input type={type} required={required} value={value} onChange={e=>set(e.target.value)}/></label>;
  }
  function liftOption(value,set) {
    return <label className="field">Лифт<select value={value} onChange={e=>set(e.target.value)}><option value="">Весь объект</option>{selected?.lifts?.filter(l=>l.can_edit).map(l=><option value={l.id} key={l.id}>{l.label}</option>)}</select></label>;
  }
  return <div className="commissioning">
    <div className="page-heading"><div><h1>Объекты наладки</h1>
      </div>
      <button disabled={busy} onClick={()=>run(async()=>{await loadList();if(selected)await openProject(selected.id);},{refresh:false})}>Обновить</button></div>
    {error&&<div className="error" role="alert">{error}</div>}
    {canManage&&<form className="panel" style={{padding:18,display:'grid',gap:12,marginBottom:18}} onSubmit={e=>{e.preventDefault();run(async()=>{
      const created=await post('commissioning/',{name:newName,address:newAddress});
      setNewName('');setNewAddress('');await openProject(created.id);
    },{refresh:false});}}>
      <h2>Новый объект наладки</h2>
      <div className="form-grid">{field('Название',newName,setNewName)}{field('Адрес',newAddress,setNewAddress)}</div>
      <button className="primary" disabled={busy||!newName.trim()||!newAddress.trim()}>Создать объект</button>
    </form>}
    <div className="parts-grid" style={{marginBottom:20}}>
      {projects.map(p=><button key={p.id} className="panel part-card" onClick={()=>run(()=>openProject(p.id),{refresh:false})}>
        <header><b>{p.name}</b><span>{p.archived?'Архив':'В работе'}</span></header>
        <p>{p.address}</p><strong>{p.progress}%</strong><small>{p.lift_count} лифтов</small>
      </button>)}
    </div>
    {!projects.length&&<p className="empty">Объекты не назначены либо ещё не созданы.</p>}
    {selected&&<div style={{display:'grid',gap:18}}>
      <section className="panel" style={{padding:20}}>
        <div className="page-heading"><div><h2>{selected.name}</h2><p>{selected.address}</p></div>
          <strong>{selected.progress}%</strong></div>
        {selected.archived&&<div className="notice">Архив: изменения запрещены, история сохранена.</div>}
        {canManage&&<form className="form-grid" style={{margin:'16px 0'}} onSubmit={e=>{e.preventDefault();run(async()=>{
          await post(`commissioning/${selected.id}/lifts/`,{label:newLift,factory_number:factory});
          setNewLift('');setFactory('');
        });}}>
          {field('Лифт / обозначение',newLift,setNewLift)}{field('Заводской номер',factory,setFactory,'text',false)}
          <button className="primary" disabled={busy||selected.archived||!newLift.trim()}>Добавить лифт</button>
        </form>}
        <div style={{display:'grid',gap:10}}>
          {selected.lifts.map(l=><article className="panel" style={{padding:16}} key={l.id}>
            <div className="actions" style={{justifyContent:'space-between'}}>
              <b>{l.label} · {l.progress}%</b><small>Зав. {l.factory_number||'—'} · изм. {l.revision}</small>
            </div>
            <p className="muted">{l.assignees.map(a=>a.name).join(', ')||'Исполнитель не назначен'}</p>
            {canManage&&!selected.archived&&<div className="actions" style={{margin:'12px 0'}}>
              <select style={{maxWidth:290}} value={assignment[l.id]||''} onChange={e=>setAssignment(s=>({...s,[l.id]:e.target.value}))}>
                <option value="">Назначить наладчика</option>
                {members.map(m=><option key={m.user} value={m.user}>{m.name} · {m.role}</option>)}
              </select>
              <button disabled={!assignment[l.id]||busy} onClick={()=>run(()=>post(`commissioning/${selected.id}/lifts/${l.id}/assign/`,{user:Number(assignment[l.id])}))}>Назначить</button>
            </div>}
            {l.can_edit&&!selected.archived&&<form className="range" style={{marginTop:12}} onSubmit={e=>{e.preventDefault();run(async()=>{
              await post(`commissioning/${selected.id}/lifts/${l.id}/progress/`,{
                progress:Number(progress[l.id]??l.progress),revision:l.revision,description:description[l.id]||''});
              setDescription(s=>({...s,[l.id]:''}));
            });}}>
              <label className="field">Готовность %<input type="number" min="0" max="100" required value={progress[l.id]??l.progress} onChange={e=>setProgress(s=>({...s,[l.id]:e.target.value}))}/></label>
              <label className="field">Что сделано<input required maxLength={4000} value={description[l.id]||''} onChange={e=>setDescription(s=>({...s,[l.id]:e.target.value}))}/></label>
              <button className="primary" disabled={busy}>Сохранить прогресс</button>
            </form>}
            <details><summary>История изменений · {l.history.length}</summary>
              {l.history.map(h=><p key={h.id} className="task-event"><small>{new Date(h.created_at).toLocaleString('ru-RU')} · {h.author} · {h.before}% → {h.after}%</small><br/>{h.description}</p>)}
            </details>
          </article>)}
        </div>
        {!selected.lifts.length&&<p className="empty">Лифты не добавлены.</p>}
      </section>
      <section className="panel" style={{padding:20}}>
        <h2>Потребности и препятствия</h2>
        {selected.needs.map(n=><div className="list-row" key={n.id}><span><b>{n.blocking?'Блокирует работы · ':''}{n.category}</b><p>{n.description}</p><small>{n.resolved?'Закрыта':'Ожидает решения'}</small></span>
          {canManage&&!n.resolved&&!selected.archived&&<button onClick={()=>run(()=>post(`commissioning/${selected.id}/needs/${n.id}/resolve/`,{}))}>Закрыть</button>}</div>)}
        {!selected.archived&&<form style={{display:'grid',gap:9,marginTop:14}} onSubmit={e=>{e.preventDefault();run(async()=>{await post(`commissioning/${selected.id}/needs/`,{category:needKind,description:needText,blocking});setNeedText('');});}}>
          <select value={needKind} onChange={e=>setNeedKind(e.target.value)}>{[['OTHER','Прочее'],['WATER','Вода в приямке'],['POWER','Электричество'],['TOOL','Инструмент'],['MATERIAL','Материалы'],['ACCESS','Доступ'],['DOCUMENT','Документы'],['INSTALLATION','Монтажные работы']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>
          {field('Что требуется',needText,setNeedText)}
          <label><input type="checkbox" checked={blocking} onChange={e=>setBlocking(e.target.checked)}/> Блокирует наладку</label>
          <button disabled={busy||!needText.trim()}>Добавить потребность</button>
        </form>}
      </section>
      <section className="panel" style={{padding:20}}>
        <h2>Дополнительные работы и согласование</h2>
        {selected.extras.map(w=><div className="list-row" key={w.id}><span><b>{w.description}</b><small>{w.price} ₽ · {w.status}</small></span>
          {canManage&&w.status==='PENDING'&&!selected.archived&&<div className="actions">
            <button onClick={()=>run(()=>post(`commissioning/${selected.id}/extra-work/${w.id}/decision/`,{approve:true}))}>Согласовать</button>
            <button onClick={()=>run(()=>post(`commissioning/${selected.id}/extra-work/${w.id}/decision/`,{approve:false}))}>Отклонить</button></div>}</div>)}
        {!selected.archived&&<form className="form-grid" style={{marginTop:12}} onSubmit={e=>{e.preventDefault();run(async()=>{await post(`commissioning/${selected.id}/extra-work/`,{description:extraText,price:extraPrice});setExtraText('');});}}>
          {field('Описание допработы',extraText,setExtraText)}{field('Сумма, ₽',extraPrice,setExtraPrice,'number')}
          <button disabled={busy||!extraText.trim()}>Предложить допработу</button>
        </form>}
      </section>
      <section className="panel" style={{padding:20}}>
        <h2>{canManage?'Расходы наладчиков':'Мои личные расходы'}</h2>
        <p className="muted">Участники общего объекта не видят чужие личные расходы и чеки.</p>
        {selected.expenses.map(e=><div className="list-row" key={e.id}>
          <span><b>{e.amount} ₽ · {e.description}</b><small>{e.author} · {e.spent_on?e.spent_on.split('-').reverse().join('.'):new Date(e.created_at).toLocaleDateString('ru-RU')}</small></span>
          {e.has_receipt&&<a className="button" href={`/api/commissioning/${selected.id}/expenses/${e.id}/receipt/`}>Чек</a>}
          {canManage&&!e.reimbursed_at&&!selected.archived&&<button onClick={()=>run(()=>post(`commissioning/${selected.id}/expenses/${e.id}/reimburse/`,{}))}>Возместить</button>}
        </div>)}
        {!canManage&&!selected.archived&&<form className="form-grid" style={{marginTop:12}} onSubmit={e=>{e.preventDefault();run(async()=>{
          let saved=expenseOperation?.project===selected.id?expenseOperation:null;
          if(!saved){
            const command={id:crypto.randomUUID(),type:'expense',body:{amount:expenseAmount,description:expenseText,lift:expenseLift?Number(expenseLift):null,spent_on:expenseDate}};
            const image=receipt?await compressImage(receipt):null;
            saved={command,image,project:selected.id};setExpenseOperation(saved);
          }
          let body=saved.command;
          if(saved.image){body=new FormData();body.append('command',JSON.stringify(saved.command));body.append('receipt',saved.image);}
          try{await post(`commissioning/${selected.id}/sync/`,body);}
          catch(err){if(err.status>=400&&err.status<500)setExpenseOperation(null);throw err;}
          setExpenseAmount('');setExpenseText('');setReceipt(null);setExpenseOperation(null);
        });}}>
          <fieldset className="form-grid" disabled={busy||expenseOperation?.project===selected.id}>
            <label className="field">Сумма, ₽<input type="number" min="0.01" step="0.01" required value={expenseAmount} onChange={e=>setExpenseAmount(e.target.value)}/></label>
            {field('Описание расхода',expenseText,setExpenseText)}
            {field('Дата расхода',expenseDate,setExpenseDate,'date')}
            {liftOption(expenseLift,setExpenseLift)}
            <label className="field">Фото чека <input type="file" accept="image/jpeg,image/png,image/webp" onChange={e=>setReceipt(e.target.files?.[0]||null)}/></label>
          </fieldset>
          <button disabled={busy||!expenseAmount||!expenseText.trim()}>Записать расход</button>
        </form>}
      </section>
      <section className="panel" style={{padding:20}}>
        <h2>Запросы новых лифтов</h2>
        {selected.lift_requests.map(r=><div className="list-row" key={r.id}><span><b>{r.label}</b><small>{r.status}</small></span>
          {canManage&&r.status==='PENDING'&&!selected.archived&&<div className="actions">
            <button onClick={()=>run(()=>post(`commissioning/${selected.id}/lift-requests/${r.id}/decision/`,{approve:true}))}>Одобрить</button>
            <button onClick={()=>run(()=>post(`commissioning/${selected.id}/lift-requests/${r.id}/decision/`,{approve:false}))}>Отклонить</button></div>}</div>)}
        {!canManage&&!selected.archived&&<form className="range" onSubmit={e=>{e.preventDefault();run(async()=>{await post(`commissioning/${selected.id}/lift-requests/`,{label:requestLabel});setRequestLabel('');});}}>
          {field('Запрос на новый лифт',requestLabel,setRequestLabel)}
          <button disabled={busy||!requestLabel.trim()}>Отправить руководителю</button>
        </form>}
      </section>
      <section className="panel" style={{padding:20}}>
        <h2>Чат объекта</h2><p className="muted">Сообщения не заменяют согласование допработ.</p>
        {selected.messages.map(m=><p key={m.id} className="task-event"><b>{m.author}</b><small> · {new Date(m.created_at).toLocaleString('ru-RU')}</small><br/>{m.text}</p>)}
        {!selected.archived&&<form className="range" onSubmit={e=>{e.preventDefault();run(async()=>{await post(`commissioning/${selected.id}/messages/`,{text:message});setMessage('');});}}>
          {field('Сообщение',message,setMessage)}
          <button disabled={busy||!message.trim()}>Отправить</button></form>}
      </section>
      {canManage&&!selected.archived&&<button className="danger" onClick={()=>{
        const reason=window.prompt('Укажите причину архивации объекта');
        if(reason?.trim())run(()=>post(`commissioning/${selected.id}/archive/`,{reason:reason.trim()}));
      }}>Архивировать объект с причиной</button>}
    </div>}
  </div>;
}
