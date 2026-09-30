import http from "node:http";
import crypto from "node:crypto";
import {Pool} from "pg";
import {
  verifyCloudPaymentsNotification,
  parseCloudPaymentsForm,
  validateCloudPaymentsPay,
} from "../billing/cloudpayments.mjs";

const PORT=Number(process.env.PORT||8080);
const MODE=String(process.env.BILLING_MODE||"sandbox").toLowerCase();
const APP_ORIGIN=String(process.env.APP_ORIGIN||"https://4k.kontrakevich.com").replace(/\/$/,"");
const CORS_ORIGIN=String(process.env.CORS_ORIGIN||APP_ORIGIN).replace(/\/$/,"");
const DATABASE_URL=process.env.DATABASE_URL||"";
const SESSION_SECRET=process.env.BILLING_SESSION_HMAC_SECRET||"";
const PERMIT_SECRET=process.env.BILLING_PERMIT_HMAC_SECRET||"";
const WORKER_SECRET=process.env.BILLING_WORKER_SHARED_SECRET||"";
const CLOUDPAYMENTS_PUBLIC_TERMINAL_ID=process.env.CLOUDPAYMENTS_PUBLIC_TERMINAL_ID||"";
const CLOUDPAYMENTS_API_SECRET=process.env.CLOUDPAYMENTS_API_SECRET||"";
const EMAIL_DELIVERY_WEBHOOK_URL=process.env.EMAIL_DELIVERY_WEBHOOK_URL||"";
const pool=new Pool({connectionString:DATABASE_URL,ssl:process.env.PGSSL==="1"?{rejectUnauthorized:false}:undefined});

const PACKS=Object.freeze({
  starter_10:{code:"starter_10",credits:10,price_rub:790,enabled:true},
  pro_30:{code:"pro_30",credits:30,price_rub:1990,enabled:true},
  studio_100:{code:"studio_100",credits:100,price_rub:5990,enabled:true},
});

function b64url(input){return Buffer.from(input).toString("base64url")}
function signToken(payload,secret){
  if(!secret)throw new Error("token_secret_missing");
  const body=b64url(JSON.stringify(payload));
  const sig=crypto.createHmac("sha256",secret).update("v1."+body).digest("base64url");
  return "v1."+body+"."+sig;
}
function verifyToken(token,secret,expectedType){
  const [v,body,sig]=String(token||"").split(".");
  if(v!=="v1"||!body||!sig)throw new Error("invalid_token");
  const expected=crypto.createHmac("sha256",secret).update(v+"."+body).digest("base64url");
  if(sig.length!==expected.length||!crypto.timingSafeEqual(Buffer.from(sig),Buffer.from(expected)))throw new Error("invalid_signature");
  const payload=JSON.parse(Buffer.from(body,"base64url").toString("utf8"));
  if(expectedType&&payload.typ!==expectedType)throw new Error("invalid_token_type");
  if(Number(payload.exp||0)<Math.floor(Date.now()/1000))throw new Error("token_expired");
  return payload;
}
function sha256(value){return crypto.createHash("sha256").update(String(value)).digest("hex")}
function normalizeEmail(value){return String(value||"").trim().toLowerCase()}
function validEmail(email){return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)&&email.length<=254}
function maskEmail(email){const [a,b]=email.split("@");return (a.slice(0,2)||"*")+"***@"+b}
function uuid(){return crypto.randomUUID()}
function json(res,status,payload){
  res.writeHead(status,{
    "content-type":"application/json; charset=utf-8",
    "cache-control":"no-store",
    "access-control-allow-origin":CORS_ORIGIN,
    "access-control-allow-credentials":"true",
    "vary":"Origin",
  });
  res.end(JSON.stringify(payload));
}
function corsPreflight(res){
  res.writeHead(204,{
    "access-control-allow-origin":CORS_ORIGIN,
    "access-control-allow-methods":"GET,POST,OPTIONS",
    "access-control-allow-headers":"content-type,authorization,x-mg4k-worker-secret",
    "access-control-max-age":"600",
    "vary":"Origin",
  });res.end();
}
async function bodyText(req,limit=1024*1024){
  const chunks=[];let size=0;
  for await(const c of req){size+=c.length;if(size>limit)throw new Error("body_too_large");chunks.push(c)}
  return Buffer.concat(chunks).toString("utf8");
}
async function bodyJson(req){const raw=await bodyText(req);return raw?JSON.parse(raw):{}}
async function tx(fn){
  const c=await pool.connect();try{await c.query("BEGIN");const v=await fn(c);await c.query("COMMIT");return v}catch(e){await c.query("ROLLBACK");throw e}finally{c.release()}
}
function bearer(req){const a=String(req.headers.authorization||"");return a.toLowerCase().startsWith("bearer ")?a.slice(7).trim():""}
function requireSession(req){
  const token=bearer(req);if(!token)throw Object.assign(new Error("auth_required"),{status:401});
  return verifyToken(token,SESSION_SECRET,"session");
}
function requireWorker(req){
  const got=String(req.headers["x-mg4k-worker-secret"]||"");
  if(!WORKER_SECRET||got.length!==WORKER_SECRET.length||!crypto.timingSafeEqual(Buffer.from(got),Buffer.from(WORKER_SECRET))){
    throw Object.assign(new Error("worker_unauthorized"),{status:403});
  }
}
async function ensureAccountByEmail(c,email){
  const emailHash=sha256(email);
  let r=await c.query("SELECT account_id FROM billing_identities WHERE email_hash=$1",[emailHash]);
  if(r.rowCount)return {accountId:r.rows[0].account_id,emailHash};
  const accountId=uuid();
  await c.query("INSERT INTO billing_accounts(id) VALUES($1)",[accountId]);
  await c.query("INSERT INTO billing_identities(account_id,email_hash,email_masked) VALUES($1,$2,$3)",[accountId,emailHash,maskEmail(email)]);
  return {accountId,emailHash};
}
async function grantTrial(c,accountId,emailHash){
  const existing=await c.query("SELECT 1 FROM trial_grants WHERE account_id=$1 OR identity_fingerprint_hash=$2 LIMIT 1",[accountId,emailHash]);
  if(existing.rowCount)return false;
  await c.query("INSERT INTO trial_grants(account_id,credits,identity_fingerprint_hash) VALUES($1,2,$2)",[accountId,emailHash]);
  await c.query("INSERT INTO credit_ledger(id,account_id,delta,reason,external_ref) VALUES($1,$2,2,'trial_grant',$3)",[uuid(),accountId,"trial:"+accountId]);
  return true;
}
async function accountState(accountId){
  const r=await pool.query(`
    SELECT
      COALESCE((SELECT SUM(delta) FROM credit_ledger WHERE account_id=$1),0)::int AS ledger,
      COALESCE((SELECT SUM(credits) FROM credit_reservations WHERE account_id=$1 AND status IN ('reserved','claimed')),0)::int AS reserved
  `,[accountId]);
  const row=r.rows[0]||{ledger:0,reserved:0};
  return {balance:Number(row.ledger)-Number(row.reserved),ledger:Number(row.ledger),reserved:Number(row.reserved)};
}
async function sendMagicLink(email,verifyUrl){
  if(MODE==="sandbox")return {sandbox:true};
  if(!EMAIL_DELIVERY_WEBHOOK_URL)throw Object.assign(new Error("email_delivery_not_configured"),{status:503});
  const r=await fetch(EMAIL_DELIVERY_WEBHOOK_URL,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({
    to:email,template:"mg4k_magic_link",subject:"Вход в MG 4K",verify_url:verifyUrl
  })});
  if(!r.ok)throw Object.assign(new Error("email_delivery_failed"),{status:502});
  return {sandbox:false};
}
async function payOrder(c,order,transactionId,payloadHash){
  const eventId=String(transactionId);
  const ins=await c.query("INSERT INTO payment_events(provider,event_id,event_type,payload_sha256,status) VALUES('cloudpayments',$1,'pay',$2,'processing') ON CONFLICT DO NOTHING RETURNING event_id",[eventId,payloadHash]);
  if(!ins.rowCount)return {duplicate:true};
  const upd=await c.query("UPDATE payment_orders SET status='paid',provider_transaction_id=$2,paid_at=now() WHERE id=$1 AND status='pending' RETURNING *",[order.id,eventId]);
  if(!upd.rowCount){await c.query("UPDATE payment_events SET status='ignored',processed_at=now() WHERE provider='cloudpayments' AND event_id=$1",[eventId]);return {duplicate:true}}
  await c.query("INSERT INTO credit_ledger(id,account_id,delta,reason,external_ref) VALUES($1,$2,$3,'purchase',$4) ON CONFLICT DO NOTHING",[uuid(),order.account_id,order.credits,eventId]);
  await c.query("UPDATE payment_events SET status='processed',processed_at=now() WHERE provider='cloudpayments' AND event_id=$1",[eventId]);
  return {duplicate:false};
}
async function route(req,res){
  if(req.method==="OPTIONS")return corsPreflight(res);
  const u=new URL(req.url,"http://billing.local");
  const path=u.pathname;

  if(path==="/health"&&req.method==="GET")return json(res,200,{ok:true,service:"mg4k-billing-gateway",mode:MODE});
  if(path==="/api/config"&&req.method==="GET")return json(res,200,{
    mode:MODE,
    trial_credits:2,
    currency:"RUB",
    packs:Object.values(PACKS),
    cloudpayments:{enabled:MODE==="live"&&Boolean(CLOUDPAYMENTS_PUBLIC_TERMINAL_ID),public_terminal_id:CLOUDPAYMENTS_PUBLIC_TERMINAL_ID||null},
  });

  if(path==="/api/auth/request-link"&&req.method==="POST"){
    const {email:returnEmail,return_url}=await bodyJson(req);
    const email=normalizeEmail(returnEmail);if(!validEmail(email))return json(res,400,{error:"invalid_email"});
    const raw=crypto.randomBytes(32).toString("base64url"),tokenHash=sha256(raw);
    const data=await tx(async c=>{
      const {accountId,emailHash}=await ensureAccountByEmail(c,email);
      await c.query("DELETE FROM auth_tokens WHERE account_id=$1 AND used_at IS NULL",[accountId]);
      await c.query("INSERT INTO auth_tokens(token_hash,account_id,email_hash,expires_at) VALUES($1,$2,$3,now()+interval '15 minutes')",[tokenHash,accountId,emailHash]);
      return {accountId,emailHash};
    });
    const returnTo=String(return_url||APP_ORIGIN).startsWith(APP_ORIGIN)?String(return_url||APP_ORIGIN):APP_ORIGIN;
    const verifyUrl=new URL("/auth/verify",String(process.env.PUBLIC_BASE_URL||"http://localhost:"+PORT));
    verifyUrl.searchParams.set("token",raw);verifyUrl.searchParams.set("return_to",returnTo);
    const delivery=await sendMagicLink(email,verifyUrl.toString());
    return json(res,200,{ok:true,sandbox:delivery.sandbox,sandbox_verify_token:delivery.sandbox?raw:undefined});
  }

  if(path==="/api/auth/verify"&&req.method==="POST"){
    const {token}=await bodyJson(req);const tokenHash=sha256(token||"");
    const session=await tx(async c=>{
      const r=await c.query("SELECT * FROM auth_tokens WHERE token_hash=$1 AND used_at IS NULL AND expires_at>now() FOR UPDATE",[tokenHash]);
      if(!r.rowCount)throw Object.assign(new Error("verification_token_invalid"),{status:400});
      const row=r.rows[0];
      await c.query("UPDATE auth_tokens SET used_at=now() WHERE token_hash=$1",[tokenHash]);
      await c.query("UPDATE billing_identities SET email_verified_at=COALESCE(email_verified_at,now()) WHERE account_id=$1",[row.account_id]);
      await grantTrial(c,row.account_id,row.email_hash);
      const ident=await c.query("SELECT email_masked FROM billing_identities WHERE account_id=$1",[row.account_id]);
      return signToken({typ:"session",sub:row.account_id,email_masked:ident.rows[0]?.email_masked||"—",exp:Math.floor(Date.now()/1000)+7*86400},SESSION_SECRET);
    });
    return json(res,200,{ok:true,session_token:session});
  }

  if(path==="/auth/verify"&&req.method==="GET"){
    const token=u.searchParams.get("token")||"",returnTo=u.searchParams.get("return_to")||APP_ORIGIN;
    const tokenHash=sha256(token);
    const session=await tx(async c=>{
      const r=await c.query("SELECT * FROM auth_tokens WHERE token_hash=$1 AND used_at IS NULL AND expires_at>now() FOR UPDATE",[tokenHash]);
      if(!r.rowCount)throw Object.assign(new Error("verification_token_invalid"),{status:400});
      const row=r.rows[0];await c.query("UPDATE auth_tokens SET used_at=now() WHERE token_hash=$1",[tokenHash]);
      await c.query("UPDATE billing_identities SET email_verified_at=COALESCE(email_verified_at,now()) WHERE account_id=$1",[row.account_id]);
      await grantTrial(c,row.account_id,row.email_hash);
      const ident=await c.query("SELECT email_masked FROM billing_identities WHERE account_id=$1",[row.account_id]);
      return signToken({typ:"session",sub:row.account_id,email_masked:ident.rows[0]?.email_masked||"—",exp:Math.floor(Date.now()/1000)+7*86400},SESSION_SECRET);
    });
    const target=new URL(String(returnTo).startsWith(APP_ORIGIN)?returnTo:APP_ORIGIN);
    target.hash="billing_session="+encodeURIComponent(session);
    res.writeHead(302,{location:target.toString(),"cache-control":"no-store"});return res.end();
  }

  if(path==="/api/account"&&req.method==="GET"){
    const session=requireSession(req);const state=await accountState(session.sub);
    return json(res,200,{account_id:session.sub,email_masked:session.email_masked,balance:state.balance,reserved:state.reserved});
  }

  if(path==="/api/generation/permit"&&req.method==="POST"){
    const session=requireSession(req);
    const result=await tx(async c=>{
      const available=await c.query(`
        SELECT
          COALESCE((SELECT SUM(delta) FROM credit_ledger WHERE account_id=$1),0)::int
          - COALESCE((SELECT SUM(credits) FROM credit_reservations WHERE account_id=$1 AND status IN ('reserved','claimed')),0)::int AS balance
      `,[session.sub]);
      if(Number(available.rows[0]?.balance||0)<1)throw Object.assign(new Error("insufficient_credits"),{status:402});
      const reservationId=uuid(),jti=uuid();
      await c.query("INSERT INTO credit_reservations(id,account_id,permit_jti,credits,status) VALUES($1,$2,$3,1,'reserved')",[reservationId,session.sub,jti]);
      const permit=signToken({typ:"generation_permit",aud:"mg4k-worker",sub:session.sub,reservation_id:reservationId,jti,credits:1,exp:Math.floor(Date.now()/1000)+600},PERMIT_SECRET);
      return {permit,reservation_id:reservationId};
    });
    return json(res,201,result);
  }

  if(path==="/api/internal/generation/claim"&&req.method==="POST"){
    requireWorker(req);const {permit,job_id}=await bodyJson(req);
    const p=verifyToken(permit,PERMIT_SECRET,"generation_permit");
    if(p.aud!=="mg4k-worker"||Number(p.credits)!==1)return json(res,403,{error:"invalid_permit"});
    const r=await pool.query("UPDATE credit_reservations SET status='claimed',job_id=$2,claimed_at=now() WHERE id=$1 AND account_id=$3 AND permit_jti=$4 AND status='reserved' RETURNING id",[p.reservation_id,job_id,p.sub,p.jti]);
    if(!r.rowCount)return json(res,409,{error:"permit_already_claimed_or_invalid"});
    return json(res,200,{ok:true,reservation_id:p.reservation_id,account_id:p.sub});
  }

  if(path==="/api/internal/generation/settle"&&req.method==="POST"){
    requireWorker(req);
    const body=await bodyJson(req);
    const outcome=await tx(async c=>{
      const r=await c.query("SELECT * FROM credit_reservations WHERE id=$1 AND job_id=$2 FOR UPDATE",[body.reservation_id,body.job_id]);
      if(!r.rowCount)throw Object.assign(new Error("reservation_not_found"),{status:404});
      const row=r.rows[0];
      if(["committed","released"].includes(row.status))return {status:row.status,idempotent:true};
      const requests=Math.max(0,Number(body.provider_request_count||0));
      const resultAvailable=Boolean(body.result_available),hardFailure=Boolean(body.hard_system_failure);
      const release=requests===0||(hardFailure&&!resultAvailable);
      if(release){
        await c.query("UPDATE credit_reservations SET status='released',settled_at=now(),settlement_reason=$2 WHERE id=$1",[row.id,requests===0?"no_provider_request":"hard_failure_without_result"]);
      }else{
        await c.query("UPDATE credit_reservations SET status='committed',settled_at=now(),settlement_reason='billable_generation_consumed' WHERE id=$1",[row.id]);
        await c.query("INSERT INTO credit_ledger(id,account_id,delta,reason,external_ref) VALUES($1,$2,$3,'generation_consumed',$4) ON CONFLICT DO NOTHING",[uuid(),row.account_id,-Number(row.credits),String(body.job_id)]);
      }
      if(requests>0){
        await c.query("INSERT INTO generation_costs(job_id,account_id,provider,provider_model,request_count,exact_cost_usd,exact_cost_complete) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(job_id) DO UPDATE SET request_count=EXCLUDED.request_count,exact_cost_usd=EXCLUDED.exact_cost_usd,exact_cost_complete=EXCLUDED.exact_cost_complete,recorded_at=now()",[
          body.job_id,row.account_id,String(body.provider||"openrouter"),body.provider_model||null,requests,body.provider_cost_usd??null,Boolean(body.provider_cost_complete)
        ]);
      }
      return {status:release?"released":"committed",idempotent:false};
    });
    return json(res,200,{ok:true,...outcome});
  }

  if(path==="/api/billing/checkout"&&req.method==="POST"){
    const session=requireSession(req);const {pack_code}=await bodyJson(req);const pack=PACKS[String(pack_code||"")];
    if(!pack?.enabled)return json(res,400,{error:"invalid_pack"});
    const orderId=uuid();
    await pool.query("INSERT INTO payment_orders(id,account_id,provider,pack_code,credits,amount_minor,currency,status) VALUES($1,$2,'cloudpayments',$3,$4,$5,'RUB','pending')",[orderId,session.sub,pack.code,pack.credits,pack.price_rub*100]);
    if(MODE==="sandbox")return json(res,201,{sandbox:true,order_id:orderId,pack});
    if(!CLOUDPAYMENTS_PUBLIC_TERMINAL_ID)return json(res,503,{error:"cloudpayments_not_configured"});
    return json(res,201,{sandbox:false,order_id:orderId,intent:{
      publicTerminalId:CLOUDPAYMENTS_PUBLIC_TERMINAL_ID,
      description:"MG 4K — "+pack.credits+" кредитов",
      amount:pack.price_rub,
      currency:"RUB",
      culture:"ru-RU",
      paymentSchema:"Single",
      externalId:orderId,
      receiptEmail:session.email_masked,
      userInfo:{accountId:session.sub}
    }});
  }

  if(path==="/api/billing/sandbox/complete"&&req.method==="POST"){
    if(MODE!=="sandbox")return json(res,404,{error:"not_found"});
    const session=requireSession(req);const {order_id}=await bodyJson(req);
    const result=await tx(async c=>{
      const r=await c.query("SELECT * FROM payment_orders WHERE id=$1 AND account_id=$2 FOR UPDATE",[order_id,session.sub]);
      if(!r.rowCount)throw Object.assign(new Error("order_not_found"),{status:404});
      const order=r.rows[0],eventId="sandbox-"+order.id;
      return payOrder(c,order,eventId,sha256(eventId));
    });
    return json(res,200,{ok:true,...result});
  }

  if(path==="/api/billing/webhook/cloudpayments"&&req.method==="POST"){
    const raw=await bodyText(req);
    if(!await verifyCloudPaymentsNotification(raw,req.headers,CLOUDPAYMENTS_API_SECRET))return json(res,403,{error:"invalid_signature"});
    const event=parseCloudPaymentsForm(raw);
    const orderR=await pool.query("SELECT * FROM payment_orders WHERE id=$1",[String(event.InvoiceId||"")]);
    if(!orderR.rowCount)return json(res,404,{error:"order_not_found"});
    const order=orderR.rows[0];
    validateCloudPaymentsPay(event,{invoiceId:order.id,accountId:order.account_id,currency:order.currency,amountMinor:Number(order.amount_minor)});
    await tx(c=>payOrder(c,order,String(event.TransactionId),sha256(raw)));
    return json(res,200,{code:0});
  }

  return json(res,404,{error:"not_found"});
}

const server=http.createServer(async(req,res)=>{
  try{await route(req,res)}
  catch(error){console.error("BILLING_ERROR",error);json(res,Number(error.status||500),{error:error.message||"internal_error"})}
});
server.listen(PORT,()=>console.log("MG4K billing gateway listening",PORT,MODE));
