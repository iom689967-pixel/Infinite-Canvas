const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/api-settings.js','utf8');
function section(start,end){return source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));}
const item={id:'personal-example',name:'old',base_url:'https://old.example',protocol:'openai',image_models:[],model_names:{}};
const input=value=>({value});
const context={item,providers:[item],provider:()=>item,nameInput:input('Kie Test'),baseInput:input('https://api.kie.ai'),protocolInput:input('kie'),
 imageRequestModeInput:input('openai'),imageEditRouteInput:input('general'),imageGenerationEndpointInput:input(''),imageEditEndpointInput:input(''),
 providerEnabledInput:{checked:true},providerPrimaryInput:{checked:false},keyInput:input('fake-secret'),
 personalSettings:true,selectedId:item.id,CLI_PROTOCOLS:new Set(),API_PROTOCOLS:['openai','kie'],
 deriveIdFromName:(_name,id)=>id,lockedRecommendedApi:()=>null,normalizeImageRequestMode:v=>v,normalizeImageEditRoute:v=>v,
 normalizeRhEntries:v=>v,applyLockedRecommendedProtocol:()=>false,applyCliProtocolDefaults:()=>{},clearVerifyResult:()=>{},updateApimartDomesticHint:()=>{},
 document:{body:{classList:{toggle(){}}},getElementById:()=>null},
 renderEditor(){context.nameInput.value=item.name;context.baseInput.value=item.base_url;context.keyInput.value='';},
 URL,showVerifyResult:()=>{},escapeHtml:String,currentProviderApiKey:()=>{throw new Error('Mock connection failure');}};
vm.createContext(context);
vm.runInContext(section('function syncEditor(){','function ensureRunningHubLists(')+section('function updateProtocolFromInput(){','function isVolcengineProvider('),context);
vm.runInContext('updateProtocolFromInput()',context);
assert.equal(item.protocol,'kie');assert.equal(item.name,'Kie Test');assert.equal(item.base_url,'https://api.kie.ai');
assert.equal(context.keyInput.value,'fake-secret');assert.equal(item.id,'personal-example');
vm.runInContext(section('async function probeAsync(){','async function testConnection(){'),context);
(async()=>{
 await vm.runInContext('probeAsync()',context);
 assert.equal(context.protocolInput.value,'kie');assert.equal(item.protocol,'kie');
 assert.equal(item.base_url,'https://api.kie.ai');
 console.log('Shared API editor draft preservation and failed probe protocol preservation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
