const ORIGIN = process.env.SELEKYT_ORIGIN || "https://stuffed-liverpool-conf-article.trycloudflare.com";
export const maxDuration = 30;
export default async function handler(req,res){
  try{
    const r=await fetch(ORIGIN.replace(/\/$/,"")+"/api/ping",{cache:"no-store"});
    const text=await r.text();
    res.status(r.status).setHeader("content-type","application/json; charset=utf-8").setHeader("cache-control","no-store").send(text);
  }catch(e){res.status(502).json({ok:false,error:"SeleKyT worker unreachable",detail:String(e.message||e)})}
}