// Read-only provider preflight. Never expose provider credentials or account totals.
export async function providerCreditPreflight(env, fetcher=fetch) {
  const minimum=Number(env.MG4K_MIN_PROVIDER_CREDITS_USD||"0.50");
  const key=String(env.OPENROUTER_API_KEY||"").trim();
  const management=String(env.OPENROUTER_MANAGEMENT_KEY||"").trim();
  const fail=(code,summary)=>({ok:false,code,summary});
  if(!Number.isFinite(minimum)||minimum<=0||!key)return fail("PROVIDER_CREDIT_CHECK_UNAVAILABLE","Проверка кредитов недоступна");
  const get=async(path,token)=>{
    const r=await fetcher("https://openrouter.ai/api/v1/"+path,{headers:{Authorization:"Bearer "+token},signal:AbortSignal.timeout(8000)});
    if(!r.ok)throw Error("provider_credit_check_"+r.status);
    const body=await r.json();
    if(!body?.data||typeof body.data!=="object")throw Error("provider_credit_check_invalid");
    return body.data;
  };
  try {
    const k=await get("key",key);
    if(k.is_free_tier===true)return fail("PROVIDER_CREDITS_EXHAUSTED","Недостаточно кредитов");
    if(k.limit_remaining!==null && (typeof k.limit_remaining!=="number"||!Number.isFinite(k.limit_remaining)))
      return fail("PROVIDER_CREDIT_CHECK_UNAVAILABLE","Проверка лимита недоступна");
    if(k.limit_remaining!==null&&k.limit_remaining<minimum)return fail("PROVIDER_CREDITS_EXHAUSTED","Недостаточно кредитов");
    if(!management)return fail("PROVIDER_CREDIT_CHECK_UNAVAILABLE","Проверка баланса недоступна");
    const c=await get("credits",management);
    if(typeof c.total_credits!=="number"||typeof c.total_usage!=="number"||
      !Number.isFinite(c.total_credits)||!Number.isFinite(c.total_usage))
      return fail("PROVIDER_CREDIT_CHECK_UNAVAILABLE","Проверка баланса недоступна");
    if(c.total_credits-c.total_usage<minimum)return fail("PROVIDER_CREDITS_EXHAUSTED","Недостаточно кредитов");
    return {ok:true,minimum_usd:minimum,account_checked:true,key_checked:true};
  }catch{return fail("PROVIDER_CREDIT_CHECK_UNAVAILABLE","Проверка кредитов недоступна");}
}

// Only resume a confirmed billing refusal/preflight HOLD; never a timeout,
// accepted generation, replacement-donor failure or result persistence issue.
export function canResumeCreditJob(job){
  return job?.status==="failed"&&job.mode==="generative"&&
    ["PROVIDER_CREDITS_EXHAUSTED","PROVIDER_CREDIT_CHECK_UNAVAILABLE"].includes(job.diagnostic_code)&&
    !job.result_key&&!job.archive_result_key&&!job.archive_final_key&&
    !job.container_result_available&&!(job.archive_donor_keys||[]).length&&
    Number(job.provider_request_count||0)<=1&&
    Number(job.provider_cost_usd||0)===0&&
    (job.diagnostic_code==="PROVIDER_CREDIT_CHECK_UNAVAILABLE"||job.diagnostic_http_status===402||job.stage==="provider_credit_hold");
}
