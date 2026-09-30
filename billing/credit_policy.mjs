export const DEFAULT_POLICY = Object.freeze({
  trialCredits: 2,
  creditsPerGeneration: 1,
  refundIfNoProviderRequest: true,
  refundIfHardFailureWithoutResult: true,
});

export function trialGrantDecision({emailVerified, alreadyGranted, abuseBlocked}) {
  if (!emailVerified) return {allowed:false, reason:"email_not_verified"};
  if (alreadyGranted) return {allowed:false, reason:"trial_already_granted"};
  if (abuseBlocked) return {allowed:false, reason:"anti_abuse_block"};
  return {allowed:true, credits:DEFAULT_POLICY.trialCredits, reason:"verified_first_trial"};
}

export function generationReservationDecision({balance, activeReservation=false}) {
  const available = Math.max(0, Math.trunc(Number(balance || 0)));
  if (activeReservation) return {allowed:false, reason:"existing_reservation"};
  if (available < DEFAULT_POLICY.creditsPerGeneration) return {allowed:false, reason:"insufficient_credits"};
  return {allowed:true, reserve:DEFAULT_POLICY.creditsPerGeneration};
}

export function settlementDecision({providerRequestCount, resultAvailable, hardSystemFailure}) {
  const requests = Math.max(0, Math.trunc(Number(providerRequestCount || 0)));
  if (requests === 0 && DEFAULT_POLICY.refundIfNoProviderRequest) {
    return {action:"release", reason:"no_provider_request"};
  }
  if (!resultAvailable && hardSystemFailure && DEFAULT_POLICY.refundIfHardFailureWithoutResult) {
    return {action:"release", reason:"hard_system_failure_without_result"};
  }
  return {action:"commit", reason:"billable_generation_consumed"};
}
