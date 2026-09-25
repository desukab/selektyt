const ORIGIN = process.env.SELEKYT_ORIGIN || "https://stuffed-liverpool-conf-article.trycloudflare.com";
export const maxDuration = 60;
export default async function handler(req,res){
  const path=String(req.query?.path||"");
  if(!path || path.includes("..") || !path.includes("/out/web/pipeline/")) return res.status(400).json({ok:false,error:"invalid artifact"});
  try{
    const r=await fetch(ORIGIN.replace(/\/$/,"")+"/api/file?path="+encodeURIComponent(path));
    if(!r.ok) return res.status(r.status).send(await r.text());
    const buf=Buffer.from(await r.arrayBuffer());
    res.status(200).setHeader("content-type",r.headers.get("content-type")||"application/octet-stream").setHeader("content-disposition",r.headers.get("content-disposition")||"attachment").setHeader("cache-control","no-store").send(buf);
  }catch(e){res.status(502).json({ok:false,error:"artifact unavailable",detail:String(e.message||e)})}
}