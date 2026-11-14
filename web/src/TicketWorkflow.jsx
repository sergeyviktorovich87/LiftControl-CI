import MediaLibrary from './MediaLibrary';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { Badge, Dialog, Field, LocationFields } from './components';
import {prepareImages} from './imageCompression';

/* Reuses existing LiftControl 0.4 components and CSS, without a redesign. */
export default function TicketWorkflow({ item = {}, data, user, onClose, onSaved, onOpenLift, onDiscuss }) {
  const existing = Boolean(item.id);
  const [ticket, setTicket] = useState(item);
  const [history, setHistory] = useState({ events: [], comments: [] });
  const [form, setForm] = useState({
    elevator: item.elevator || '',
    title: item.title || '',
    description: item.description || '',
    priority: item.priority || 'NORMAL',
  });
  const [note, setNote] = useState('');
  const [repair, setRepair] = useState('');
  const [files, setFiles] = useState([]);
  const [reason, setReason] = useState('');
  const [stopReason, setStopReason] = useState('');
  const [assigned, setAssigned] = useState(item.assigned_to || '');
  const [additional, setAdditional] = useState([]);
  const createdLifts = useRef(new Set());
  const currentLift = data.elevators.find(e => e.id === Number(form.elevator));
  const eligible = data.technicians.filter(t => t.active && t.can_receive_tasks !== false);
  const [assignees, setAssignees] = useState({});
  const [assignReason, setAssignReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showMedia,setShowMedia]=useState(false);
  const [info, setInfo] = useState('');

  const refresh = useCallback(async () => {
    if (!existing) return;
    const [t, h] = await Promise.all([
      api('tickets/' + item.id + '/'),
      api('tickets/' + item.id + '/history/'),
    ]);
    setTicket(t);
    setHistory(h);
    setAssigned(t.assigned_to || '');
  }, [existing, item.id]);

  useEffect(() => {
    if (!existing) return;
    refresh().catch(e => setError(e.message));
    const interval = window.setInterval(() => {
      if (!document.hidden) refresh().catch(() => {});
    }, 10000);
    return () => window.clearInterval(interval);
  }, [existing, refresh]);

  const run = async fn => {
    if (busy) return;
    setBusy(true);
    setError('');
    setInfo('');
    try {
      const next = await fn();
      if (!existing) { await onSaved(); onClose(); return; }
      await refresh();
      await onSaved();
      if (next) setInfo(next);
    } catch (e) {
      setError(e.message || 'Операция не выполнена');
    } finally {
      setBusy(false);
    }
  };

  const create = async e => {
    e.preventDefault();
    await run(async () => {
      for (const elevator of [Number(form.elevator), ...additional]) {
        if (createdLifts.current.has(elevator)) continue;
        const lift = data.elevators.find(e => e.id === elevator);
        const assignee = assignees[elevator] ?? lift?.assigned_technician;
        await api('tickets/', { method: 'POST', body: {
          elevator, title: form.title.trim(), description: form.description.trim(), priority: form.priority,
          ...(assignee ? {assigned_to: Number(assignee)} : {}),
        }});
        createdLifts.current.add(elevator);
      }
    });
  };

  const complete = async () => {
    await run(async () => {
      if (repair.trim().length < 3) throw Error('Заполните выполненные работы.');
      if (files.length + (ticket.photos || []).length > 8) throw Error('Допускается максимум 8 фотографий.');
      // Upload first: a failed upload must never falsely mark the ticket complete.
      for (const file of files) {
        if (file.size > 10 * 1024 * 1024) throw Error('Фотография больше 10 МБ.');
        const body = new FormData();
        body.append('file', file);
        await api('tickets/' + ticket.id + '/photos/', { method: 'POST', body });
        // Never upload the same successful attachment again on a retry.
        setFiles(previous => previous.filter(candidate => candidate !== file));
      }
      await api('tickets/' + ticket.id + '/complete/', {
        method: 'POST', body: { work_description: repair.trim() },
      });
      setRepair('');
      return 'Работы сохранены, заявка выполнена.';
    });
  };

  const canComment = ['NEW', 'IN_PROGRESS', 'STOPPED'].includes(ticket.status)
    && (user.can_manage || user.on_duty || ticket.can_complete);

  return <Dialog wide title={existing ? 'Заявка #' + ticket.id : 'Новая заявка'} onClose={onClose}>
    {!existing ? <form onSubmit={create} className="editor-fields">
      {!user.can_create_ticket && <p className="notice">Создание заявок доступно диспетчеру или дежурному механику.</p>}
      <fieldset className="form-grid" disabled={!user.can_create_ticket || busy}>
        <LocationFields data={data} value={form.elevator}
          onChange={v => {setForm(f => ({ ...f, elevator: v }));setAdditional([]);}} />
        {currentLift && <div className="span-all">
          {data.elevators.filter(e => e.active && e.building === currentLift.building && e.id !== currentLift.id).map(e => <label key={e.id}><input type="checkbox" checked={additional.includes(e.id)} onChange={event => setAdditional(previous => event.target.checked ? [...previous,e.id] : previous.filter(id => id !== e.id))}/> Также лифт {e.factory_number || e.inventory_number || e.id} · подъезд {e.entrance || '—'} · {e.model || 'модель не указана'} · {e.capacity_kg ? e.capacity_kg+' кг' : ''}</label>)}
          {!!additional.length && <p>Для каждого лифта будет отдельная заявка и история ремонта.</p>}
          {[currentLift.id,...additional].map(id => <Field key={id} label={additional.length ? 'Исполнитель · лифт '+(data.elevators.find(e=>e.id===id)?.factory_number || data.elevators.find(e=>e.id===id)?.inventory_number || id) : 'Исполнитель'}>
            <select value={assignees[id] ?? (eligible.some(t => t.id === data.elevators.find(e=>e.id===id)?.assigned_technician) ? data.elevators.find(e=>e.id===id)?.assigned_technician : '')} onChange={e => setAssignees(previous => ({...previous,[id]:e.target.value}))}>
              <option value="">Не назначен — выберите механика</option>{eligible.map(t=><option key={t.id} value={t.id}>{t.full_name}</option>)}
            </select>
          </Field>)}
        </div>}
        <Field label="Неисправность">
          <input required maxLength={200} value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />
        </Field>
        <Field label="Приоритет">
          <select value={form.priority} onChange={e => setForm(f => ({ ...f, priority: e.target.value }))}>
            <option value="NORMAL">Обычный</option><option value="HIGH">Высокий</option>
            <option value="ENTRAPMENT">Застревание — люди в кабине</option><option value="EMERGENCY">Аварийный</option><option value="LOW">Низкий</option>
          </select>
        </Field>
        <div className="span-all"><Field label="Описание">
          <textarea maxLength={2000} value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
        </Field></div>
      </fieldset>
      {error && <p className="error" role="alert">{error}</p>}
      <footer className="form-actions"><button type="button" onClick={onClose}>Отмена</button>
        <button className="primary" disabled={busy || !user.can_create_ticket}>Создать заявку</button>
      </footer>
    </form> : <>
      <div className="panel">
        <div className="panel-heading">
          <div><h2>{ticket.title}</h2><p>{ticket.address} · лифт {(ticket.elevator_label||ticket.elevator_factory_number)}</p></div>
          <Badge value={ticket.status} />
          {ticket.priority==='ENTRAPMENT'&&<Badge value="ENTRAPMENT">Застревание — люди в кабине</Badge>}
        </div>
        <div className="dialog-content">
          <p>{ticket.description || 'Описание не указано.'}</p>
          <button type="button" onClick={()=>onOpenLift?.(ticket.elevator)}>Открыть карточку лифта</button> {onDiscuss&&<button type="button" onClick={onDiscuss}>Обсудить в чате</button>}
          <p className="muted small">Автор: {ticket.created_by_name || 'Ранее созданная заявка'} ·
            Исполнитель: {ticket.technician_name || 'Не назначен'}</p>
          {ticket.repair && <div className="notice"><b>Выполненные работы:</b> {ticket.repair}</div>}
          {ticket.stopped_at && <div className="notice"><b>{ticket.status==='DONE'?'Остановка в истории:':'Лифт остановлен:'}</b> {ticket.stop_reason}
            <small>{new Date(ticket.stopped_at).toLocaleString('ru-RU',{timeZone:user.timezone})}</small></div>}
          {ticket.cancellation_reason && <div className="notice">
            <b>Причина отмены:</b> {ticket.cancellation_reason}</div>}
        </div>
      </div>

      <button type="button" onClick={()=>setShowMedia(v=>!v)}>Вложения: фото и видео</button>{showMedia&&<MediaLibrary user={user} ticket={ticket.id}/>}
      {ticket.can_accept && <div className="form-actions">
        <button className="primary" disabled={busy}
          onClick={() => run(async () => {
            await api('tickets/' + ticket.id + '/accept/', { method: 'POST' });
            return 'Заявка принята вами в работу.';
          })}>Принять заявку</button>
      </div>}

      {ticket.can_stop && <section className="panel dialog-content">
        <h2>Не удалось восстановить работу</h2>
        <Field label="Причина остановки"><textarea maxLength={1000} value={stopReason} onChange={e=>setStopReason(e.target.value)} placeholder="Почему лифт остаётся остановленным и что требуется для ремонта"/></Field>
        <footer className="form-actions"><button type="button" className="danger" disabled={busy||stopReason.trim().length<5}
          onClick={()=>run(async()=>{await api(`tickets/${ticket.id}/stop/`,{method:'POST',body:{reason:stopReason.trim()}});setStopReason('');return 'Остановка сохранена. Заявка остаётся открытой.';})}>Лифт остановлен</button></footer>
      </section>}
      {ticket.can_resume && <div className="form-actions"><button disabled={busy} onClick={()=>run(async()=>{await api(`tickets/${ticket.id}/resume/`,{method:'POST'});return 'Ремонт продолжен. Лифт остаётся остановленным до завершения ремонта.';})}>Продолжить ремонт</button></div>}

      {ticket.can_complete && <section className="panel dialog-content">
        <h2>Завершение ремонта</h2>
        <Field label="Выполненные работы">
          <textarea value={repair} maxLength={4000} onChange={e => setRepair(e.target.value)}
            placeholder="Что сделано и как восстановлена работа лифта" />
        </Field>
        <Field label="Фотографии ремонта (необязательно)">
          <input type="file" multiple accept="image/jpeg,image/png,image/webp" disabled={busy}
            onChange={async e => {const selected=Array.from(e.target.files||[]);e.target.value='';if(selected.length>8){setError('До 8 фотографий.');return;}setBusy(true);try{setFiles(await prepareImages(selected));setError('');}catch(e){setError(e.message);}finally{setBusy(false);}}} />
        </Field>
        <small>{files.length} фотографий выбрано. После завершения записи нельзя удалять.</small>
        <footer className="form-actions"><button type="button" className="primary"
          disabled={busy || repair.trim().length < 3} onClick={complete}>Выполнено</button></footer>
      </section>}

      {canComment && <section className="panel dialog-content">
        <h2>Добавить комментарий</h2>
        <Field label="Запись остаётся в истории и не редактируется">
          <textarea maxLength={4000} value={note} onChange={e => setNote(e.target.value)} />
        </Field>
        <footer className="form-actions"><button type="button" disabled={busy || !note.trim()}
          onClick={() => run(async () => {
            await api('tickets/' + ticket.id + '/comment/', { method: 'POST', body: { body: note.trim() } });
            setNote('');
            return 'Комментарий сохранён.';
          })}>Добавить комментарий</button></footer>
      </section>}

      {ticket.status === 'NEW' && (user.can_manage || user.on_duty) && <section className="panel dialog-content">
        <h2>Назначить замену</h2>
        <Field label="Механик">
          <select value={assigned} onChange={e => setAssigned(e.target.value)}>
            <option value="">Выберите механика</option>
            {eligible.map(t =>
              <option key={t.id} value={t.id}>{t.full_name}</option>)}
          </select>
        </Field>
        <Field label="Причина переназначения">
          <textarea value={assignReason} maxLength={1000}
            onChange={e => setAssignReason(e.target.value)} />
        </Field>
        <footer className="form-actions"><button type="button" disabled={busy || !assigned || assignReason.trim().length < 5}
          onClick={() => run(async () => {
            await api('tickets/' + ticket.id + '/assign/', { method: 'POST',
              body: { assigned_to: Number(assigned), reason: assignReason.trim() } });
            setAssignReason('');
            return 'Исполнитель изменён с записью причины.';
          })}>Назначить</button></footer>
      </section>}

      {ticket.can_cancel && <section className="panel dialog-content">
        <h2>Отменить заявку</h2>
        <p className="muted small">Заявка, комментарии и фотографии останутся в истории лифта.</p>
        <Field label="Обязательная причина">
          <textarea value={reason} maxLength={1000}
            onChange={e => setReason(e.target.value)} />
        </Field>
        <footer className="form-actions"><button type="button" disabled={busy || reason.trim().length < 5}
          onClick={() => run(async () => {
            await api('tickets/' + ticket.id + '/cancel/', { method: 'POST', body: { reason: reason.trim() } });
            setReason('');
            return 'Заявка отменена. Причина сохранена.';
          })}>Отменить с причиной</button></footer>
      </section>}

      {!!(ticket.photos || []).length && <section className="panel dialog-content">
        <h2>Фотографии</h2><div className="photos">
          {ticket.photos.map(p => <figure key={p.id}>
            <a href={p.url} target="_blank" rel="noreferrer">
              <img src={p.url} alt={'Фото ремонта ' + p.id} />
            </a>
            <figcaption><small>{p.uploaded_by || 'Исполнитель'} ·
              {p.created_at ? ' ' + new Date(p.created_at).toLocaleString('ru-RU') : ''}</small></figcaption>
          </figure>)}
        </div>
        <small>Фото являются частью истории заявки, удаление запрещено.</small>
      </section>}

      <section className="panel dialog-content">
        <h2>История заявки</h2>
        {[...(history.events || [])].reverse().map(ev =>
          <div className="list-row" key={'ev-' + ev.id}>
            <span><b>{({
              created: 'Создана', accepted: 'Принята в работу', completed: 'Выполнена',
              canceled: 'Отменена', reassigned: 'Переназначена', commented: 'Комментарий',
              stopped: 'Лифт остановлен', resumed: 'Ремонт продолжен',
            })[ev.action] || ev.action}</b>
              <small>{ev.actor} · {new Date(ev.created_at).toLocaleString('ru-RU')}</small>
              {ev.reason && <p>{ev.reason}</p>}
            </span>
          </div>)}
        {(history.comments || []).map(c =>
          <div className="list-row" key={'comment-' + c.id}>
            <span><b>{c.kind === 'RESULT' ? 'Результат ремонта' : 'Комментарий'}</b>
              <small>{c.author} · {new Date(c.created_at).toLocaleString('ru-RU')}</small>
              <p>{c.body}</p>
            </span>
          </div>)}
        {!history.events?.length && !history.comments?.length && <p className="muted">Записей пока нет.</p>}
      </section>
      {error && <p className="error" role="alert">{error}</p>}
      {info && <p className="notice" role="status">{info}</p>}
      <footer className="form-actions"><button onClick={onClose}>Закрыть</button></footer>
    </>}
  </Dialog>;
}

