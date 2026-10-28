let dashboardData = {alerts:[], vehicles:[], allocations:[], totals:{}, hourly:[], hotspots:[]};
let hourChart = null;
const $ = id => document.getElementById(id);
function esc(v){return String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtDate(s){try{return new Date(s).toLocaleString()}catch{return s||'—'}}
function typeLabel(t){return ({crowd:'Crowd report',overcrowding:'Overcrowding',congestion:'Congestion',medical_emergency:'Medical emergency',driver_injured:'Driver injured',vehicle_breakdown:'Vehicle breakdown',safety_threat:'Safety threat',door_obstruction:'Door obstruction',other:'Other incident'})[t]||t}
function renderAlerts(){
 const filter=$('alertFilter').value;
 const list=dashboardData.alerts.filter(a=>filter==='ALL'||a.status===filter);
 $('alertRows').innerHTML=list.length?list.map(a=>`<tr>
 <td><span class="priority ${esc(a.severity)}">${esc(a.severity.toUpperCase())}</span></td>
 <td><strong>${esc(typeLabel(a.event_type))}</strong><br><small>${esc(fmtDate(a.created_at))}</small><br><small>${esc(a.source)}</small></td>
 <td><strong>${esc(a.vehicle_id)}</strong><br><small>${esc(a.route)}</small></td>
 <td>${esc(a.stop_name)}<br><small>${esc(a.message)}</small></td>
 <td>${a.occupancy==null?'—':esc(a.occupancy)}${a.capacity==null?'':` / ${esc(a.capacity)}`}</td>
 <td><span class="status ${esc(a.status)}">${esc(a.status)}</span></td>
 <td><div class="row-actions">${a.status==='NEW'?`<button class="mini-btn" onclick="setAlertStatus(${a.id},'ACKNOWLEDGED')">Acknowledge</button>`:''}${a.status!=='RESOLVED'?`<button class="mini-btn" onclick="setAlertStatus(${a.id},'RESOLVED')">Resolve</button>`:''}<button class="mini-btn" onclick="deleteAlert(${a.id})">Delete</button>${['crowd','overcrowding','congestion'].includes(a.event_type)?`<button class="mini-btn" onclick="prepareAllocation(${a.id})">Allocate bus</button>`:''}</div></td>
 </tr>`).join(''):`<tr><td colspan="7" class="empty">No alerts match this filter.</td></tr>`;
 $('allocationAlert').innerHTML='<option value="">No linked alert</option>'+dashboardData.alerts.filter(a=>a.status!=='RESOLVED').map(a=>`<option value="${a.id}">#${a.id} · ${esc(a.vehicle_id)} · ${esc(typeLabel(a.event_type))}</option>`).join('');
}
function renderVehicles(){
 const available=dashboardData.vehicles.filter(v=>v.status==='AVAILABLE');
 $('availableBuses').textContent=available.length;
 $('fleetCount').textContent=`${dashboardData.vehicles.length} VEHICLES`;
 $('allocationVehicle').innerHTML=available.length?available.map(v=>`<option value="${esc(v.vehicle_id)}">${esc(v.vehicle_id)} — ${esc(v.last_location)}</option>`).join(''):'<option value="">No available buses</option>';
 $('fleetList').innerHTML=dashboardData.vehicles.map(v=>`<div class="fleet-item"><div><strong>${esc(v.vehicle_id)}</strong><small>${esc(v.route)} · ${esc(v.driver_name||'Driver not assigned')} · ${esc(v.last_location)}</small></div><span class="fleet-status ${esc(v.status)}">${esc(v.status)}</span></div>`).join('')||'<p class="empty">No vehicles registered.</p>';
}
function renderAllocations(){
 $('allocationList').innerHTML=dashboardData.allocations.map(a=>`<div class="assignment"><div><strong>${esc(a.vehicle_id)} → ${esc(a.route)}</strong><small>${esc(a.stop_name||'Route assignment')} · ${a.driver_username?`Driver @${esc(a.driver_username)}`:'Driver not linked'} · ETA ${esc(a.eta_minutes)} min · ${esc(a.assigned_to)}</small></div><button class="mini-btn" type="button" onclick="notifyAllocation(${a.id})">Notify driver</button><select aria-label="Assignment status" onchange="updateAllocation(${a.id},this.value)"><option ${a.status==='ASSIGNED'?'selected':''}>ASSIGNED</option><option ${a.status==='EN_ROUTE'?'selected':''}>EN_ROUTE</option><option ${a.status==='ARRIVED'?'selected':''}>ARRIVED</option><option ${a.status==='COMPLETED'?'selected':''}>COMPLETED</option><option ${a.status==='CANCELLED'?'selected':''}>CANCELLED</option></select></div>`).join('')||'<p class="empty">No bus assignments yet.</p>';
}
function renderHotspots(){
 $('hotspotList').innerHTML=dashboardData.hotspots.map(h=>`<div class="hotspot"><div><strong>${esc(h.stop_name)}</strong><small>${esc(h.route)} · Last: ${esc(fmtDate(h.last_seen))}</small></div><span class="tag">${h.events} reports</span></div>`).join('')||'<p class="empty">No crowd history yet. Incoming crowd reports will build this list.</p>';
 const ctx=$('hourChart');
 if(hourChart)hourChart.destroy();
 hourChart=new Chart(ctx,{type:'bar',data:{labels:Array.from({length:24},(_,i)=>`${String(i).padStart(2,'0')}:00`),datasets:[{label:'Crowd reports',data:dashboardData.hourly,backgroundColor:'#5472f5',borderRadius:5,maxBarThickness:20}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{maxTicksLimit:8}},y:{beginAtZero:true,ticks:{precision:0},grid:{color:'#edf0f6'}}}}});
}
async function loadDashboard(){
 try{
  const res=await fetch('/api/dashboard');if(!res.ok)throw new Error('Could not load dashboard');
  dashboardData=await res.json();
  $('activeAlerts').textContent=dashboardData.totals.active_alerts;
  $('emergencies').textContent=dashboardData.totals.emergencies;
  $('crowdEvents').textContent=dashboardData.totals.crowd_events;
  renderAlerts();renderVehicles();renderAllocations();renderHotspots();
 }catch(e){console.error(e);alert('Could not load authority data. Check the Flask terminal.');}
}
async function setAlertStatus(id,status){
 const res=await fetch(`/api/alerts/${id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});
 const d=await res.json();if(!res.ok){alert(d.error||'Update failed');return}
 if(d.status_notification&&!d.status_notification.sent)alert(`Alert status updated, but the driver was not notified: ${d.status_notification.error}`);
 loadDashboard();
}
async function deleteAlert(id){if(!confirm(`Delete alert #${id}? This cannot be undone.`))return;await fetch(`/api/alerts/${id}`,{method:'DELETE'});loadDashboard();}
async function clearHistory(){if(!confirm('Clear ALL alert history? This cannot be undone.'))return;const r=await fetch('/api/alerts',{method:'DELETE'});if(r.ok)loadDashboard();}
function prepareAllocation(id){$('allocationAlert').value=String(id);const a=dashboardData.alerts.find(x=>x.id===id);if(a){$('allocationRoute').value=a.route;$('allocationDriver').value=a.driver_username||'';$('allocationEta').focus()}$('dispatch').scrollIntoView({behavior:'smooth'});}
$('allocationAlert').addEventListener('change',()=>{const alert=dashboardData.alerts.find(a=>String(a.id)===$('allocationAlert').value);$('allocationDriver').value=alert?alert.driver_username||'':''});
async function updateAllocation(id,status){const r=await fetch(`/api/allocations/${id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});const d=await r.json();if(!r.ok)alert(d.error||'Could not update assignment');loadDashboard();}
async function notifyAllocation(id){
 const allocation=dashboardData.allocations.find(a=>a.id===id);
 const username=(allocation&&allocation.driver_username)||prompt('Enter the driver portal username to notify about this assignment:');
 if(!username)return;
 const response=await fetch(`/api/allocations/${id}/notify`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({driver_username:username})});
 const data=await response.json();
 if(!response.ok){alert(data.error||'Could not notify driver');return}
 if(data.driver_notification&&data.driver_notification.sent)alert(`Assignment notification sent to @${data.driver_username}.`);
 else alert(`Assignment saved, but driver notification failed: ${(data.driver_notification&&data.driver_notification.error)||'Unknown callback error'}`);
 loadDashboard();
}
$('allocationForm').addEventListener('submit',async e=>{
 e.preventDefault();const payload={alert_id:$('allocationAlert').value||null,vehicle_id:$('allocationVehicle').value,driver_username:$('allocationDriver').value,route:$('allocationRoute').value,eta_minutes:$('allocationEta').value,assigned_to:$('assignedTo').value};
 const r=await fetch('/api/allocations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();
 if(!r.ok)$('allocationMsg').textContent=d.error||'Allocation failed.';
 else if(d.driver_notification&&d.driver_notification.sent)$('allocationMsg').textContent=`Bus ${d.vehicle_id} assigned to ${d.route}. Driver @${d.driver_username} notified.`;
 else $('allocationMsg').textContent=`Bus ${d.vehicle_id} assigned to ${d.route}, but driver @${d.driver_username} was not notified: ${(d.driver_notification&&d.driver_notification.error)||'Unknown callback error'}`;
 if(r.ok)loadDashboard();
});
$('vehicleForm').addEventListener('submit',async e=>{
 e.preventDefault();const payload={vehicle_id:$('newVehicleId').value,route:$('newVehicleRoute').value,status:'AVAILABLE',last_location:'Depot'};
 const r=await fetch('/api/vehicles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();
 if(!r.ok){alert(d.error||'Could not add vehicle');return}
 $('newVehicleId').value='';loadDashboard();
});
$('driverForm').addEventListener('submit',async e=>{
 e.preventDefault();
 const submitButton=e.currentTarget.querySelector('button[type="submit"]');
 const credentials=$('driverCredentials');
 const message=$('driverMsg');
 credentials.hidden=true;
 message.textContent='Creating driver login…';
 submitButton.disabled=true;
 try{
  const response=await fetch('/api/drivers/provision',{
   method:'POST',
   headers:{
    'Content-Type':'application/json',
    'Authorization':`Bearer ${$('driverProvisioningKey').value}`
   },
   body:JSON.stringify({username:$('driverUsername').value,display_name:$('driverDisplayName').value})
  });
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'Could not create driver login.');
  $('createdDriverUsername').textContent=result.username;
  $('createdDriverPassword').textContent=result.password;
  credentials.hidden=false;
  message.textContent='Driver account created. Save the temporary password before leaving this page.';
  $('driverUsername').value='';
  $('driverDisplayName').value='';
 }catch(error){
  message.textContent=error.message||'Could not create driver login.';
 }finally{
  $('driverProvisioningKey').value='';
  submitButton.disabled=false;
 }
});
$('copyDriverCredentials').addEventListener('click',async()=>{
 const text=`Username: ${$('createdDriverUsername').textContent}\nPassword: ${$('createdDriverPassword').textContent}`;
 try{
  await navigator.clipboard.writeText(text);
  $('driverMsg').textContent='Credentials copied. Share them securely with the driver.';
 }catch{
  $('driverMsg').textContent='Could not copy credentials. Select and copy them manually.';
 }
});
function tick(){ $('clock').textContent=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}); }
tick();setInterval(tick,1000);loadDashboard();setInterval(loadDashboard,15000);
