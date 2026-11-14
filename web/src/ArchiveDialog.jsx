import React,{useState} from 'react';
import {api} from './api';
import {Dialog,Field} from './components';
export default function ArchiveDialog({type,item,data,onClose,onSaved}){
 const [reason,setReason]=useState(''),[replacement,setReplacement]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const staff=type==='technicians',document=type==='documents';
 const save=async e=>{e.preventDefault();setBusy(true);setError('');try{await api(document?`documents/${item.id}/`:`${type}/${item.id}/archive/`,{method:document?'DELETE':'POST',body:{reason:reason.trim(),replacement:replacement?Number(replacement):null}});await onSaved();onClose();}catch(e){setError(e.message);}finally{setBusy(false);}};
 return <Dialog title={document?'Удалить документ из библиотеки':staff?'Удалить сотрудника из рабочих списков':'Удалить лифт из рабочих списков'} onClose={onClose}>
 <form onSubmit={save}><p>{document?item.title:staff?item.full_name:item.address+' · '+(item.factory_number||item.inventory_number||item.id)}</p>
 <p>{document?'Документ исчезнет из общей библиотеки и автоподбора для лифтов. Файл, предыдущие версии и причина удаления останутся в истории.':'Запись перейдёт в архив. История заявок, ремонта и ТО сохранится.'}{staff?' Доступ сотрудника к этой организации будет отключён.':''}</p>
 <Field label="Причина"><textarea required minLength={5} maxLength={1000} value={reason} onChange={e=>setReason(e.target.value)}/></Field>
 {staff&&<Field label="Кому передать лифты и будущие дежурства"><select value={replacement} onChange={e=>setReplacement(e.target.value)}><option value="">Без передачи (если назначений нет)</option>{data.technicians.filter(t=>t.active&&t.id!==item.id).map(t=><option key={t.id} value={t.id}>{t.full_name}</option>)}</select></Field>}
 {error&&<p className="error" role="alert">{error}</p>}<footer className="form-actions"><button type="button" onClick={onClose}>Отмена</button><button className="danger" disabled={busy||reason.trim().length<5}>Переместить в архив</button></footer></form></Dialog>;
}
