export const roles={ADMIN:'Администратор',DIRECTOR:'Директор',DISPATCHER:'Диспетчер',MECHANIC:'Механик'};
let profile={id:1,name:'Демо-администратор',role:'ADMIN',organization:'ООО «Организация Бета»',timezone:'Asia/Yakutsk',technician:null,can_manage:true,can_export_chat:true,is_superuser:true};
export function previewRole(role){profile={...profile,role,can_manage:role!=='MECHANIC',can_export_chat:role==='ADMIN',technician:role==='MECHANIC'?1:null};return {...profile};}
const people=['Александр Иванов','Владимир Петров','Игорь Кузнецов','Александр Смирнов'];
const buildings=[['Учебная улица','265',6],['Учебная улица','265/2',4],['','Учебный комплекс',19],['','ЖК «Дом Альфа»',8],['','ЖК «Дом Бета»',12],['пер. Учебный','84',2],['Учебный проспект','13',4],['Тестовая улица','21',3],['Учебная','213',4],['','ЖК «Демонстрационный»',8]].map(([street,house,count],i)=>({id:i+1,street,house:street?house:'',address:street?street+', '+house:house,name:street?'Жилой дом':house,count,management_company:''}));
let n=0;
const elevators=buildings.flatMap(b=>Array.from({length:b.count},(_,i)=>({id:++n,building:b.id,address:b.address,factory_number:'DEMO-'+String(n).padStart(4,'0'),manufacturer:b.id===1?'МЭЛ':'BLT',model:b.id===1?'МЭЛ':'Monarch',entrance:String(Math.floor(i/2)+1),floors:17,capacity_kg:1000,assigned_technician:(b.id%4)+1,technician_name:people[b.id%4],active:true,notes:''})));
const db={tasks:[],buildings,elevators,technicians:people.map((full_name,i)=>({id:i+1,full_name,active:true,role:'MECHANIC',role_display:'Механик'})),tickets:[{id:182,elevator:52,title:'Посторонний шум при движении',description:'Обращение по шуму в шахте. Требуется осмотр.',priority:'HIGH',status:'NEW',assigned_to:null,repair:'',photos:[],created_at:'2026-11-06T09:56:00+09:00'},{id:184,elevator:1,title:'Не закрываются двери кабины',description:'Проверить работу привода дверей.',priority:'HIGH',status:'IN_PROGRESS',assigned_to:1,repair:'',photos:[],created_at:'2026-11-06T10:42:00+09:00'}],maintenance:[{id:1,elevator:1,technician:1,kind:'TO1',planned_date:'2026-11-06',notes:'',completed_at:null}],duty:Array.from({length:31},(_,i)=>({id:i+1,technician:(i%4)+1,start:new Date(Date.UTC(2026,9,31+i,-1)).toISOString(),end:new Date(Date.UTC(2026,10,1+i,-1)).toISOString(),kind:'daily'})),parts:[{id:1,elevator:1,part:'Ролик двери кабины',quantity:2,notes:'Проверить размер по образцу',status:'NEW',requested_by:2,author_name:'Александр Иванов',created_at:'2026-11-06T08:00:00+09:00'}],chat:[{id:1,body:'Общий чат команды. Здесь можно обсудить работы и передать информацию дежурному.',author:1,author_name:'Демо-администратор',created_at:'2026-11-06T08:00:00+09:00'}],documents:[{id:1,title:'МЭЛ — руководство по обслуживанию',manufacturer:'МЭЛ',model:'',url:'',created_at:'2026-11-01'}],audit:[]};
let serial=300, version=0, draft=null;
function enrich(name,row){const e=db.elevators.find(x=>x.id===Number(row.elevator));return {...row,...(e?{address:e.address,elevator_factory_number:e.factory_number}:{}),technician_name:people[Number(row.technician||row.assigned_to||row.assigned_technician)-1]||'',...(name==='buildings'?{elevators_count:db.elevators.filter(e=>e.building===row.id).length}:{})};}
export async function demoRequest(path,options={}) {
 const method=options.method||'GET';const [resource,id,action,photoId]=path.split('?')[0].split('/');const body=options.body||{};const params=new URLSearchParams(path.split('?')[1]||'');
 if(path==='me/')return {...profile};
 if(resource==='logout')return {ok:true};
 if(resource==='dashboard')return {buildings:db.buildings.length,elevators:db.elevators.length,technicians:4,open_tickets:db.tickets.filter(t=>!['DONE','CANCELED'].includes(t.status)).length,maintenance_today:1};
 if(resource==='chat'&&id==='export') {
  if(!profile.can_export_chat)throw Error('Выгрузка чата доступна только администратору.');
  const safe=t=>'"'+String(t).replaceAll('"','""')+'"';
  return new Blob(['\ufeffДата,Автор,Сообщение\n'+db.chat.filter(m=>m.created_at.slice(0,10)>=params.get('from')&&m.created_at.slice(0,10)<=params.get('to')).map(m=>[m.created_at,m.author_name,/^[\s]*[=+@-]/.test(m.body)?"'"+m.body:m.body].map(safe).join(',')).join('\n')],{type:'text/csv'});
 }
 if(resource==='duty'&&id==='preview') {
  const {from,to,time='08:00',hours=24,every=1,technicians,replace}=body;
  const first=new Date(from+'T00:00:00+09:00'),last=new Date(to+'T00:00:00+09:00');const days=(last-first)/86400000+1;
  if(!(days>=1&&days<=366)||!technicians?.length||!(hours>0&&hours<=24)||!(every>=1&&every<=31))throw Error('Проверьте период, сотрудников и длительность.');
  const removed=replace?db.duty.filter(s=>new Date(s.start)>=first&&new Date(s.start)<new Date(+last+86400000)):[];
  const rest=db.duty.filter(s=>!removed.includes(s)),added=[];let skipped=0;
  for(let k=0,i=0;k<days;k+=Number(every),i++) {const day=new Date(+first+k*86400000+9*3600000).toISOString().slice(0,10),start=new Date(day+'T'+time+':00+09:00'),end=new Date(+start+Number(hours)*3600000);if(rest.some(s=>new Date(s.start)<end&&new Date(s.end)>start)){if(replace)throw Error('Пересечение с переходящей сменой за границами периода.');skipped++;continue;}added.push({technician:Number(technicians[i%technicians.length]),technician_id:Number(technicians[i%technicians.length]),start:start.toISOString(),end:end.toISOString(),kind:body.kind||'daily'});}
  draft={token:'preview-'+version,added,removed,version};return {token:draft.token,added,replaced:removed.length,skipped};
 }
 if(resource==='duty'&&id==='apply') {if(!draft||draft.token!==body.token||draft.version!==version)throw Error('Обновите предпросмотр.');db.duty=db.duty.filter(s=>!draft.removed.includes(s)).concat(draft.added.map(s=>({...s,id:++serial})));version++;return {ok:true};}
 if(resource==='elevators'&&action==='documents'){const e=db.elevators.find(x=>x.id===Number(id));return db.documents.filter(d=>d.manufacturer===e.manufacturer&&(!d.model||d.model===e.model));}
 if(resource==='tickets'&&action==='photos') {const ticket=db.tickets.find(t=>t.id===Number(id));if(method==='DELETE'){ticket.photos=ticket.photos.filter(p=>p.id!==Number(photoId));return null;}const file=body.get('file');if(!file||!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>10*1024*1024)throw Error('Выберите изображение до 10 МБ.');if(ticket.photos.length>=8)throw Error('Максимум 8 фотографий.');const p={id:++serial,url:URL.createObjectURL(file)};ticket.photos.push(p);return p;}
 const rows=db[resource];if(!rows)throw Error('Раздел недоступен.');
 if(method==='GET'){let filtered=id?rows.filter(x=>x.id===Number(id)):rows;if(resource==='duty'&&params.get('from'))filtered=filtered.filter(s=>new Date(s.end)>new Date(params.get('from')+'T00:00:00+09:00')&&new Date(s.start)<new Date(+new Date(params.get('to')+'T00:00:00+09:00')+86400000));return id?enrich(resource,filtered[0]):filtered.map(x=>enrich(resource,x));}
 if(resource==='duty'&&!profile.can_manage)throw Error('Недостаточно прав.');
 if(resource==='duty'&&method!=='DELETE'){if(new Date(body.end)<=new Date(body.start)||(+new Date(body.end)-new Date(body.start))>86400000)throw Error('Длительность — до 24 часов.');if(rows.some(s=>s.id!==Number(id)&&s.technician===Number(body.technician)&&new Date(s.start)<new Date(body.end)&&new Date(s.end)>new Date(body.start)))throw Error('У сотрудника уже есть пересекающееся дежурство.');}
 if(resource==='tickets'&&body.status==='DONE'&&(!body.repair?.trim()||!(body.assigned_to||rows.find(x=>x.id===Number(id))?.assigned_to)))throw Error('Заполните выполненные работы и исполнителя.');
 if(method==='POST'){const data=body instanceof FormData?Object.fromEntries(body):body;const row={...data,id:++serial,created_at:new Date().toISOString(),author:profile.id,requested_by:profile.id,author_name:profile.name,photos:[]};if(resource==='documents')row.url=URL.createObjectURL(body.get('file'));rows.push(row);version++;return enrich(resource,row);}
 if(method==='PATCH'){const row=rows.find(x=>x.id===Number(id));Object.assign(row,body);version++;return enrich(resource,row);}
 if(method==='DELETE'){db[resource]=rows.filter(x=>x.id!==Number(id));version++;return null;}
 throw Error('Действие недоступно.');
}
