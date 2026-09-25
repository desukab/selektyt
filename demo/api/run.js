const ORIGIN = process.env.SELEKYT_ORIGIN || "https://stuffed-liverpool-conf-article.trycloudflare.com";
export const maxDuration = 300;
export default async function handler(req,res){
  if(req.method!=="POST") return res.status(405).json({ok:false,error:"POST required"});
  let body;
  try{body=typeof req.body==="object"?req.body:JSON.parse(req.body||"{}")}catch{return res.status(400).json({ok:false,error:"invalid JSON"})}
  const source=String(body.source||"").trim();
  let u;
  try{u=new URL(source)}catch{return res.status(400).json({ok:false,error:"invalid source URL"})}
  if(!/^https?:$/.test(u.protocol)) return res.status(400).json({ok:false,error:"only http(s) video URLs are allowed"});
  const payload={source,model:"small",limit:4,chunk_minutes:5,beam_size:1,caption_style:"minimal",audio_analysis:false,export_fcpxml:true};
  try{
    const r=await fetch(ORIGIN.replace(/\/$/,"")+"/api/run_pipeline",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(payload)});
    const data=await r.json();
    if(!r.ok||!data.ok) return res.status(r.status||502).json({ok:false,error:data.error||"SeleKyT pipeline failed",upstream:data});
    const base="/api/artifact?path=";
    const artifacts=(data.data?.artifacts||[]).map(p=>({path:p,url:base+encodeURIComponent(p),label:String(p).split(/[\\/]/).pop()}));
    return res.status(200).json({ok:true,data:{...data.data,artifacts}});
  }catch(e){return res.status(502).json({ok:false,error:"SeleKyT worker unreachable",detail:String(e.message||e)})}
}