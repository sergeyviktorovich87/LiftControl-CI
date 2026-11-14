import React, {useEffect,useState} from 'react';
import {api,selectOrganization} from './api';
import {Field} from './components';
const labels={ADMIN:'Администратор',DIRECTOR:'Директор',DISPATCHER:'Диспетчер',MECHANIC:'Механик',ADJUSTER:'Внешний наладчик'};
export async function switchOrganization(id){
  await api('organizations/switch/',{method:'POST',body:{organization:Number(id)}});
  selectOrganization(Number(id));
  location.href='/?organization='+Number(id)+'#home';
}
export default function OrganizationControls({user,data={technicians:[]},onChanged=async()=>{}}){
  const [name,setName]=useState(''),[code,setCode]=useState(''),[role,setRole]=useState('MECHANIC'),[tech,setTech]=useState('');
  const [staffName,setStaffName]=useState(''),[staffLogin,setStaffLogin]=useState(''),[staffPassword,setStaffPassword]=useState(''),[staffPhone,setStaffPhone]=useState(''),[staffRole,setStaffRole]=useState('MECHANIC'),[existingStaff,setExistingStaff]=useState(''),[staffMode,setStaffMode]=useState('account');
  const [error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[invite,setInvite]=useState(null),[members,setMembers]=useState([]),[invites,setInvites]=useState([]),[available,setAvailable]=useState([]);
  const refresh=async()=>{
    if(user?.can_manage_access){const [rows,codes]=await Promise.all([api('organizations/members/'),api('organizations/invites/')]);setMembers(rows);setInvites(codes);}
  };
  useEffect(()=>{api('organizations/').then(result=>{setAvailable(result.organizations);return refresh();}).catch(e=>setError(e.message));},[user?.organization_id]);
  const run=async fn=>{setBusy(true);setError('');setMessage('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}};
  const join=e=>{e.preventDefault();run(async()=>{const result=await api('organizations/join/',{method:'POST',body:{code:code.trim()}});await switchOrganization(result.organization_id);});};
  return <>
    <div className="setting-row"><div><h2>Организации</h2><p>Одна учётная запись для основной работы и совместительства. Права, заявки, чат и график отдельные в каждой компании.</p>
      {(user?.organizations||available).map(o=><button type="button" disabled={busy||o.id===user?.organization_id} key={o.id} onClick={()=>run(()=>switchOrganization(o.id))}>{o.name} · {labels[o.role]}{o.id===user?.organization_id?' · выбрана':''}</button>)}
      <form className="access-form" onSubmit={join}><Field label="Код приглашения в организацию"><input value={code} required maxLength={43} autoComplete="off" onChange={e=>setCode(e.target.value)}/></Field><button disabled={busy||!code.trim()}>Присоединиться</button></form>
    </div></div>
    {user?.can_manage_access&&<>
      <div className="setting-row"><div><h2>Добавить организацию</h2><p>Вы станете её администратором. Новая компания создаётся с пустым списком объектов.</p><form className="access-form" onSubmit={e=>{e.preventDefault();run(async()=>{const next=await api('organizations/',{method:'POST',body:{name,timezone:user.timezone}});await switchOrganization(next.id);});}}><Field label="Название организации"><input required minLength={2} maxLength={200} value={name} onChange={e=>setName(e.target.value)}/></Field><button disabled={busy}>Создать</button></form></div></div>
      <div className="setting-row" id="employee-create"><div><h2>Добавить сотрудника</h2><p>Создайте сотрудника и доступ в этой организации. Если у человека уже есть аккаунт для другой компании, используйте приглашение ниже — второй логин не нужен.</p>
      <form className="access-form" onSubmit={e=>{e.preventDefault();run(async()=>{
        if(staffMode==='account'){
          await api('organizations/members/create/',{method:'POST',body:{username:staffLogin.trim(),password:staffPassword,full_name:staffName.trim(),phone:staffPhone.trim(),role:staffRole,technician:existingStaff?Number(existingStaff):null}});
          setMessage('Сотрудник и учётная запись созданы. Передайте данные для входа лично.');
        }else{
          await api('technicians/',{method:'POST',body:{full_name:staffName.trim(),phone:staffPhone.trim(),role:'MECHANIC',active:true}});
          setMessage('Карточка создана без доступа. Для входа создайте аккаунт или приглашение.');
        }
        setStaffPassword('');setStaffLogin('');setStaffName('');setStaffPhone('');setExistingStaff('');
        await refresh();await onChanged();
      });}}>
        <Field label="Тип записи"><select value={staffMode} onChange={e=>{setStaffMode(e.target.value);setExistingStaff('');}}><option value="account">Сотрудник с логином и паролем</option><option value="card">Только карточка (без входа)</option></select></Field>
        {staffMode==='account'&&<Field label="Карточка сотрудника"><select value={existingStaff} onChange={e=>{const id=e.target.value;setExistingStaff(id);const item=data.technicians.find(t=>String(t.id)===id);if(item)setStaffName(item.full_name);}}><option value="">Создать новую карточку</option>{data.technicians.filter(t=>t.active&&!members.some(m=>m.technician===t.id)).map(t=><option key={t.id} value={t.id}>{t.full_name} · без логина</option>)}</select></Field>}
        <Field label="ФИО сотрудника"><input required minLength={2} maxLength={200} value={staffName} onChange={e=>setStaffName(e.target.value)}/></Field>
        <Field label="Телефон"><input type="tel" maxLength={50} value={staffPhone} onChange={e=>setStaffPhone(e.target.value)}/></Field>
        {staffMode==='account'&&<>
          <Field label="Логин"><input required minLength={3} maxLength={150} autoComplete="off" value={staffLogin} onChange={e=>setStaffLogin(e.target.value)}/></Field>
          <Field label="Пароль (от 12 символов)"><input required type="password" minLength={12} autoComplete="new-password" value={staffPassword} onChange={e=>setStaffPassword(e.target.value)}/></Field>
          <Field label="Роль в организации"><select value={staffRole} onChange={e=>setStaffRole(e.target.value)}>{Object.entries(labels).filter(([id])=>id!=='ADMIN'||user.role==='ADMIN').map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></Field>
        </>}
        <button disabled={busy}>{staffMode==='account'?'Создать сотрудника и доступ':'Создать карточку'}</button>
      </form></div></div>
      <div className="setting-row"><div><h2>Пригласить сотрудника</h2><p>Выберите роль и карточку механика. Одноразовый код действует 7 дней. Сотрудник вводит его после входа в свою учётную запись.</p><form className="access-form" onSubmit={e=>{e.preventDefault();run(async()=>{setInvite(await api('organizations/invites/',{method:'POST',body:{role,technician:tech?Number(tech):null}}));await refresh();});}}><Field label="Роль в организации"><select value={role} onChange={e=>setRole(e.target.value)}>{Object.entries(labels).filter(([id])=>id!=='ADMIN'||user.role==='ADMIN').map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></Field><Field label="Карточка сотрудника"><select value={tech} required={role==='MECHANIC'} onChange={e=>setTech(e.target.value)}><option value="">Без карточки</option>{data.technicians.filter(t=>t.active&&!members.some(m=>m.technician===t.id)).map(t=><option key={t.id} value={t.id}>{t.full_name}</option>)}</select></Field><button disabled={busy}>Создать код</button></form>
      {invite&&<div className="notice"><b>Код: </b><code>{invite.code}</code><button type="button" onClick={()=>run(async()=>{await navigator.clipboard.writeText(invite.code);setMessage('Код скопирован');})}>Копировать код</button><small>Показывается один раз. Передайте сотруднику лично.</small></div>}
      {invites.filter(i=>!i.used_at&&!i.revoked_at&&new Date(i.expires_at)>new Date()).map(i=><div className="list-row" key={i.id}><span>Приглашение #{i.id} · {labels[i.role]}</span><button disabled={busy} onClick={()=>run(async()=>{await api(`organizations/invites/${i.id}/revoke/`,{method:'POST'});await refresh();setInvite(null);})}>Отозвать</button></div>)}
      </div></div>
      <div className="setting-row"><div><h2>Доступ сотрудников</h2><p>Отзыв доступа действует только для этой организации.</p>{members.map(m=><Member key={m.id} member={m} user={user} technicians={data.technicians} busy={busy} save={body=>run(async()=>{await api(`organizations/members/${m.id}/`,{method:'PATCH',body});await refresh();})}/>)}</div></div>
    </>}
    {error&&<p className="error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
  </>;
}
function Member({member,user,technicians,busy,save}){
 const [role,setRole]=useState(member.role),[tech,setTech]=useState(member.technician||'');
 return <div className="member-access"><b>{member.name}{member.active?'':' · доступ отключён'}</b><select aria-label={'Роль '+member.name} value={role} onChange={e=>setRole(e.target.value)}>{Object.entries(labels).filter(([r])=>r!=='ADMIN'||user.role==='ADMIN'||member.role==='ADMIN').map(([r,l])=><option key={r} value={r}>{l}</option>)}</select><select aria-label={'Карточка '+member.name} value={tech} onChange={e=>setTech(e.target.value)}><option value="">Без карточки</option>{technicians.map(t=><option key={t.id} value={t.id}>{t.full_name}</option>)}</select><button disabled={busy||member.user===user.id} onClick={()=>save({role,technician:tech?Number(tech):null})}>Сохранить</button><button disabled={busy||member.user===user.id} onClick={()=>save({active:!member.active})}>{member.active?'Отключить доступ':'Вернуть доступ'}</button></div>;
}
