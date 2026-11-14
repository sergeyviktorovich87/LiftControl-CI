import React,{useState} from 'react';
import {api} from './api';
import {Dialog,Field} from './components';
export default function ArchiveDialog({type,item,data,onClose,onSaved}){
 const [reason,setReason]=useState(''),[replacement,setReplacement]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [perLift,setPerLift]=useState(false),[assignments,setAssignments]=useState({});
 const staff=type==='technicians',document=type==='documents';
 const lifts=staff?data.elevators.filter(l=>l.active&&l.assigned_technician===item.id):[];
 const choices=data.technicians.filter(t=>t.active&&t.id!==item.id&&t.can_receive_tasks!==false);
 const save=async e=>{e.preventDefault();setBusy(true);setError('');try{
   const body={reason:reason.trim(),replacement:replacement?Number(replacement):null};
   if(perLift)body.lift_assignments=Object.fromEntries(lifts.map(l=>[l.id,assignments[l.id]||null]));
   await api(document?`documents/${item.id}/`:`${type}/${item.id}/archive/`,{method:document?'DELETE':'POST',body});
   await onSaved();onClose();
 }catch(e){setError(e.message);}finally{setBusy(false);}};
 return <Dialog title={document?'Удалить документ из библиотеки':staff?'Удалить сотрудника из рабочих списков':'Удалить лифт из рабочих списков'} onClose={onClose}>
 <form onSubmit={save}><p>{document?item.title:staff?item.full_name:item.address+' · '+(item.factory_number||item.inventory_number||item.id)}</p>
 <p>{document?'Документ исчезнет из общей библиотеки и автоподбора для лифтов. Файл, предыдущие версии и причина удаления останутся в истории.':'Запись перейдёт в архив. История заявок, ремонта и ТО сохранится.'}{staff?' Доступ сотрудника к этой организации будет отключён.':''}</p>
 <Field label="Причина"><textarea required minLength={5} maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)}/></Field>
 {staff&&<><Field label={perLift?'Кому передать будущие дежурства':'Кому передать лифты и будущие дежурства'}><select value={replacement} onChange={e=>setReplacement(e.target.value)}><option value="">Без передачи смен (если их нет)</option>{choices.map(t=><option key={t.id} value={t.id}>{t.full_name}</option>)}</select></Field>
 {lifts.length>0&&<label><input type="checkbox" checked={perLift} onChange={e=>setPerLift(e.target.checked)}/> Указать исполнителя для каждого лифта</label>}
 {perLift&&lifts.map(l=><Field key={l.id} label={`${l.address||'Объект'} · лифт ${l.factory_number||l.inventory_number||l.id}`}>
   <select value={assignments[l.id]||''} onChange={e=>setAssignments({...assignments,[l.id]:Number(e.target.value)})}><option value="">Оставить без назначения</option>{choices.map(t=><option key={t.id} value={t.id}>{t.full_name}</option>)}</select>
 </Field>)}</>}
 {error&&<p className="error" role="alert">{error}</p>}<footer className="form-actions"><button type="button" onClick={onClose}>Отмена</button><button className="danger" disabled={busy||reason.trim().length<5}>Переместить в архив</button></footer></form></Dialog>;
}
