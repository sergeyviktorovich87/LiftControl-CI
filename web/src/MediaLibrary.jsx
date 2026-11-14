import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';
import {prepareImages} from './imageCompression';
import {Field} from './components';

export default function MediaLibrary({user,elevator,ticket,source,onTicket}) {
 const [rows,setRows]=useState([]),[next,setNext]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[stats,setStats]=useState(null);
 const [filters,setFilters]=useState({address:'',type:'',author:'',from:'',to:'',elevator:elevator||'',ticket:ticket||''}),[link,setLink]=useState(null),[kind,setKind]=useState('ticket'),[target,setTarget]=useState(''),[category,setCategory]=useState('OTHER');
 const seq=useRef(0),input=useRef();
 const load=async(more=false)=>{const generation=++seq.current;setBusy(true);setError('');try{
  const query=new URLSearchParams(Object.entries({...filters,...(source?{source}:{}),...(more&&next!=null?{offset:next}:{})}).filter(([,v])=>v!==''));
  const result=await api('media/?'+query);
  if(generation===seq.current){setRows(old=>more?[...old,...result.results]:result.results);setNext(result.next);}
  if(user.can_manage_access){const s=await api('media/stats/');if(generation===seq.current)setStats(s);}
 }catch(e){if(generation===seq.current)setError(e.message);}finally{if(generation===seq.current)setBusy(false);}};
 useEffect(()=>{load();return()=>{seq.current++;};},[user.organization_id,elevator,ticket,source]);
 const change=(name,value)=>setFilters(old=>({...old,[name]:value}));
 const upload=async event=>{const files=[...event.target.files];event.target.value='';if(!files.length)return;setBusy(true);setError('');try{
  const prepared=await prepareImages(files);if(prepared.length>5||prepared.reduce((n,f)=>n+f.size,0)>20*1024*1024)throw Error('До 5 файлов и 20 МБ на отправку.');
  for(const file of prepared){const body=new FormData();body.append('file',file);body.append('upload_id',crypto.randomUUID());body.append('kind',ticket?'ticket':filters.elevator?'elevator':'library');if(ticket||filters.elevator)body.append('target_id',ticket||filters.elevator);await api('media/uploads/',{method:'POST',body});}
  await load();
 }catch(e){setError(e.message);}finally{setBusy(false);}};
 const applyLink=async event=>{event.preventDefault();setBusy(true);try{await api(`media/${link.id}/links/`,{method:'POST',body:{kind,target_id:Number(target),category}});setLink(null);await load();}catch(e){setError(e.message);}finally{setBusy(false);}};
 const groups={};for(const row of rows){const month=new Intl.DateTimeFormat('ru-RU',{timeZone:user.timezone,month:'long',year:'numeric'}).format(new Date(row.created_at));const related=row.links.find(l=>l.kind==='ticket'&&(!filters.elevator||l.elevator===Number(filters.elevator)));const key=month+(elevator&&related?' · заявка #'+related.id:'');(groups[key]??=[]).push(row);}
 const messageUrl=id=>{const url=new URL(location.href);url.hash='chat';url.searchParams.set('message',id);return url.pathname+url.search+url.hash;};
 return <section className="media-library"><header className="page-heading"><div><h2>{source==='chat'?'Вложения чата':'Фото и видео'}</h2><p>Фото и видео из заявок, чатов и работ по лифтам.</p></div><label className={'button '+(busy?'disabled':'')}>Добавить фото / видео<input ref={input} className="media-upload" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm" multiple onChange={upload} disabled={busy}/></label></header>
 {stats&&<p className="notice">Учтено {(stats.total/1024/1024).toFixed(1)} МБ: фото {(stats.photos/1024/1024).toFixed(1)} МБ, видео {(stats.videos/1024/1024).toFixed(1)} МБ, миниатюры {(stats.previews/1024/1024).toFixed(1)} МБ. Автоудаление выключено.</p>}
 <form className="media-filters" onSubmit={e=>{e.preventDefault();load();}}>
 <Field label="Адрес"><input value={filters.address} onChange={e=>change('address',e.target.value)}/></Field>
 {!elevator&&<Field label="Номер лифта"><input inputMode="numeric" type="number" min="1" value={filters.elevator} onChange={e=>change('elevator',e.target.value)}/></Field>}
 {!ticket&&<Field label="Номер заявки"><input inputMode="numeric" type="number" min="1" value={filters.ticket} onChange={e=>change('ticket',e.target.value)}/></Field>}
 <Field label="Тип файла"><select value={filters.type} onChange={e=>change('type',e.target.value)}><option value="">Фото и видео</option><option value="image">Фото</option><option value="video">Видео</option></select></Field>
 <Field label="Автор"><select value={filters.author} onChange={e=>change('author',e.target.value)}><option value="">Все авторы</option>{[...new Map(rows.filter(r=>r.author).map(r=>[r.author,r.author_name])).entries()].map(([id,name])=><option value={id} key={id}>{name}</option>)}</select></Field>
 <Field label="С"><input type="date" value={filters.from} onChange={e=>change('from',e.target.value)}/></Field><Field label="По"><input type="date" value={filters.to} onChange={e=>change('to',e.target.value)}/></Field><button disabled={busy}>Показать</button>
 </form>{error&&<p className="error" role="alert">{error}</p>}
 {Object.entries(groups).map(([group,items])=><section key={group}><h3>{group}</h3><div className="media-grid">{items.map(row=><article key={row.id} className="panel media-card">
 {row.content_type.startsWith('video/')?<video src={row.url} controls playsInline preload="none" poster={row.preview_url||undefined} aria-label={row.name}/>:<a href={row.url} target="_blank" rel="noreferrer"><img src={row.preview_url||row.url} alt={row.name} loading="lazy"/></a>}
 <b>{row.name}</b><small>{row.author_name} · {(row.size/1024/1024).toFixed(2)} МБ · на сервере</small>
 {row.captured_at&&<small>Снято: {new Date(row.captured_at).toLocaleString('ru-RU',{timeZone:user.timezone})}</small>}
 {row.links.map(l=><div key={l.kind+':'+l.id} className="small">{l.kind==='ticket'?`Заявка #${l.id}`:l.kind==='elevator'?`Лифт #${l.id}`:l.kind==='message'?`Чат · сообщение #${l.id}`:`Задание #${l.id}`} {l.address}{l.entrance?' · подъезд '+l.entrance:''}{l.kind==='message'&&<a href={messageUrl(l.id)}>Исходное сообщение</a>}{l.kind==='ticket'&&onTicket&&<button onClick={()=>onTicket(l.id)}>Открыть заявку</button>}</div>)}
 <div className="actions"><a href={row.url+'&download=1'}>Скачать</a><button onClick={()=>{setLink(row);setTarget('');}}>Связать с заявкой / лифтом</button></div>
 </article>)}</div></section>)}
 {!busy&&!rows.length&&<p className="empty">Материалов по выбранным условиям пока нет.</p>}{busy&&<p>Загрузка…</p>}{next!=null&&<button disabled={busy} onClick={()=>load(true)}>Показать ещё</button>}
 {link&&<form className="panel dialog-content" onSubmit={applyLink}><h3>Связать: {link.name}</h3><div className="media-filters"><Field label="Связь"><select value={kind} onChange={e=>setKind(e.target.value)}><option value="ticket">Заявка</option><option value="elevator">Лифт</option></select></Field><Field label="Номер записи"><input type="number" min="1" required value={target} onChange={e=>setTarget(e.target.value)}/></Field><Field label="Категория"><select value={category} onChange={e=>setCategory(e.target.value)}><option value="OTHER">Прочее</option><option value="DEFECT">Неисправность</option><option value="PROGRESS">Ход работ</option><option value="RESULT">Результат ремонта</option></select></Field></div><div className="actions"><button type="button" onClick={()=>setLink(null)}>Отмена</button><button className="primary" disabled={busy}>Связать</button></div></form>}
 </section>;
}
