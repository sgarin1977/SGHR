export const profileFields = ['legal_name','commercial_name','registered_address_raw','operational_address_raw','phone','email','website','signatory_name','signatory_position'];
export const settingsFields = ['display_name_override','phone_override','email_override','website_override','document_header_text','document_footer_text','accent_color'];
const PROFILE='/api/v1/organization/company-profile', SETTINGS='/api/v1/construction/settings/company';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const version=v=>Number.isSafeInteger(v)&&v>0;
export class Problem extends Error {constructor(status,code){super('Operation unavailable');this.status=status;this.code=code;}}
export function transport(fetcher){return async(path,{method='GET',body,bearer}={})=>{
  if(!new RegExp('^/api/v1/(?:auth/(?:email/(?:request|verify)|logout)|me|construction/ui/company-capabilities|construction/settings/company|organization/company-profile|files/(?:upload-request|[0-9a-f-]{36}/(?:complete|download-url)))$').test(path))throw new Problem(0,'invalid_path');
  const headers={Accept:'application/json'};if(body!==undefined)headers['Content-Type']='application/json';if(bearer)headers.Authorization='Bearer '+bearer;
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  try{const response=await fetcher(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',signal:controller.signal});let data;try{data=await response.json();}catch{throw new Problem(response.status,'invalid_response');}if(!response.ok)throw new Problem(response.status,data?.error?.code||'unavailable');if(!data||!Object.hasOwn(data,'data'))throw new Problem(0,'invalid_response');return data.data;}catch(error){if(error instanceof Problem)throw error;throw new Problem(0,'network');}finally{clearTimeout(timer);}
};}
export function storageUrl(raw,origin,upload){let url;try{url=new URL(raw);}catch{throw new Problem(0,'invalid_storage');}if(!origin||url.origin!==origin||url.protocol!=='https:'||url.username||url.password||url.hash||!url.pathname.startsWith(upload?'/storage/v1/object/upload/sign/':'/storage/v1/object/sign/')||!url.searchParams.get('token'))throw new Problem(0,'invalid_storage');return url.href;}
export function createCompanyFlow({request,upload,onChange=()=>{}}){
  let bearer='',refresh='',challenge=null,email='',state={phase:'email',busy:false,message:'',profile:null,settings:null,caps:null,profileRevision:0,settingsRevision:0};
  const snapshot=()=>structuredClone(state);
  const emit=()=>onChange(snapshot());
  async function api(path,options={}){return request(path,{...options,bearer});}
  function fail(error){state.message=error.code==='file'?'file':error.code==='invalid_storage'?'storage':error.status===409?'conflict':error.status===403?'forbidden':error.status===422?'invalid':error.status===429?'rate':'failed';if(error.status===401){bearer='';refresh='';state.phase='email';state.profile=null;state.settings=null;state.caps=null;state.message='expired';}}
  async function task(work){if(state.busy)return;state.busy=true;state.message='';emit();try{await work();}catch(error){fail(error);}finally{state.busy=false;emit();}}
  function validRow(row,caps){if(row!==null&&(!row||row.tenant_id!==caps.tenant_id||!version(row.row_version)))throw new Problem(0,'invalid_response');return row;}
  async function load(){
    const me=await api('/api/v1/me');const caps=await api('/api/v1/construction/ui/company-capabilities');
    if(!UUID.test(me.id)||me.status!=='active'||!UUID.test(caps.tenant_id)||caps.tenant_id!==me.tenant_id||typeof caps.can_manage_profile!=='boolean'||typeof caps.can_manage_settings!=='boolean'||!Array.isArray(caps.image_types)||!Number.isSafeInteger(caps.image_max_size_bytes)||caps.image_max_size_bytes<=0)throw new Problem(0,'invalid_response');
    const [profile,settings]=await Promise.all([api(PROFILE),api(SETTINGS)]);
    state.caps=caps;state.profile=validRow(profile,caps);state.settings=validRow(settings,caps);state.profileRevision++;state.settingsRevision++;state.phase='ready';
  }
  function clean(fields,values){const output={};for(const key of fields){const value=values[key]??'';if(typeof value!=='string')throw new Problem(422,'invalid');output[key]=value.trim()?(['document_header_text','document_footer_text'].includes(key)?value:value.trim()):null;}return output;}
  async function logo(file){
    const caps=state.caps;if(!caps.can_manage_profile||!caps.storage_origin)throw new Problem(403,'forbidden');
    const extension='.'+String(file.name).split('.').pop().toLowerCase();const type=caps.image_types.find(x=>x.extension===extension);
    if(!type||file.size<=0||file.size>caps.image_max_size_bytes||(file.type&&file.type!==type.mime_type))throw new Problem(422,'file');
    const reserved=await api('/api/v1/files/upload-request',{method:'POST',body:{object_type:'organization',entity_id:caps.tenant_id,filename:file.name,mime_type:type.mime_type,size_bytes:file.size}});
    if(!UUID.test(reserved.id))throw new Problem(0,'invalid_response');
    const url=storageUrl(reserved.upload_url,caps.storage_origin,true);
    await upload(url,file,type.mime_type);
    const ready=await api('/api/v1/files/'+reserved.id+'/complete',{method:'POST'});
    if(ready.id!==reserved.id||ready.status!=='ready')throw new Problem(422,'file');
    return ready.id;
  }
  return {snapshot,
    async sendCode(value){return task(async()=>{email=String(value).trim();if(email.length<3||email.length>320)throw new Problem(422,'invalid');const challengeData=await api('/api/v1/auth/email/request',{method:'POST',body:{email,challenge_type:'otp'}});if(!UUID.test(challengeData?.challenge_id))throw new Problem(0,'invalid_response');challenge=challengeData;state.phase='code';state.message='sent';});},
    async verify(secret,language='en'){return task(async()=>{if(!challenge)return;let tokens;try{tokens=await api('/api/v1/auth/email/verify',{method:'POST',body:{challenge_id:challenge.challenge_id,email,secret,language_code:language}});}catch(error){if(error.code==='invalid_email_challenge'){state.message='invalid';return;}throw error;}if(typeof tokens.access_token!=='string'||!tokens.access_token||tokens.token_type?.toLowerCase()!=='bearer')throw new Problem(0,'invalid_response');bearer=tokens.access_token;refresh=typeof tokens.refresh_token==='string'?tokens.refresh_token:'';challenge=null;state.phase='authenticated';await load();});},
    async reload(){return task(async()=>{if(bearer)await load();});},
    async save(section,values,{file=null,clearLogo=false}={}){return task(async()=>{
      if(state.phase!=='ready'||!['profile','settings'].includes(section)||!state.caps?.[section==='profile'?'can_manage_profile':'can_manage_settings'])throw new Problem(403,'forbidden');
      const fields=section==='profile'?profileFields:settingsFields,base=state[section],logoField=section==='profile'?'logo_file_id':'logo_file_id_override';
      const payload=clean(fields,values);
      if(section==='profile'&&!payload.legal_name)throw new Problem(422,'invalid');
      if(payload.accent_color&&!/^#[0-9a-f]{6}$/i.test(payload.accent_color))throw new Problem(422,'invalid');
      if(file&&clearLogo)throw new Problem(422,'invalid');
      if(file)payload[logoField]=await logo(file);else if(clearLogo)payload[logoField]=null;
      if(base){for(const key of Object.keys(payload)){if(payload[key]===(base[key]??null))delete payload[key];}if(!Object.keys(payload).length){state.message='unchanged';return;}payload.expected_row_version=base.row_version;}
      else if(section==='settings'&&Object.values(payload).every(v=>v===null)){state.message='unchanged';return;}
      const result=await api(section==='profile'?PROFILE:SETTINGS,{method:base?'PATCH':'POST',body:payload});
      const row=validRow(result,state.caps);if(!row||(section==='profile'&&!UUID.test(row.id)))throw new Problem(0,'invalid_response');
      state[section]=row;state[section+'Revision']++;state.message='saved';
    });},
    async downloadLogo(id){if(!UUID.test(id)||!state.caps?.storage_origin)return null;const data=await api('/api/v1/files/'+id+'/download-url');if(!state.caps.image_types.some(t=>t.mime_type===data.mime_type))throw new Problem(422,'file');return storageUrl(data.download_url,state.caps.storage_origin,false);},
    async logout(){return task(async()=>{if(refresh)await api('/api/v1/auth/logout',{method:'POST',body:{refresh_token:refresh}});bearer='';refresh='';challenge=null;state.profile=null;state.settings=null;state.caps=null;state.phase='email';state.message='';});},
  };
}
