const enc = new TextEncoder();

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, Math.min(bytes.length, i + chunk)));
  }
  return btoa(binary);
}

function headerValue(headers, name) {
  if (!headers) return "";
  if (typeof headers.get === "function") return headers.get(name) || "";
  const lower = name.toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (String(key).toLowerCase() === lower) return String(value || "");
  }
  return "";
}

function constantTimeEqual(a, b) {
  const left = enc.encode(String(a || ""));
  const right = enc.encode(String(b || ""));
  let diff = left.length ^ right.length;
  const size = Math.max(left.length, right.length);
  for (let i = 0; i < size; i++) {
    diff |= (left[i % Math.max(1,left.length)] || 0) ^ (right[i % Math.max(1,right.length)] || 0);
  }
  return diff === 0;
}

export async function cloudPaymentsHmac(rawBody, apiSecret) {
  if (!apiSecret) throw new Error("cloudpayments_api_secret_required");
  const key = await crypto.subtle.importKey("raw", enc.encode(apiSecret), {name:"HMAC", hash:"SHA-256"}, false, ["sign"]);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(String(rawBody))));
  return bytesToBase64(signature);
}

export async function verifyCloudPaymentsNotification(rawBody, headers, apiSecret) {
  const received = headerValue(headers, "Content-HMAC");
  if (!received) return false;
  const expected = await cloudPaymentsHmac(rawBody, apiSecret);
  return constantTimeEqual(received.trim(), expected);
}

export function parseCloudPaymentsForm(rawBody) {
  return Object.fromEntries(new URLSearchParams(String(rawBody)));
}

function moneyToMinor(value) {
  const normalized = String(value ?? "").replace(",", ".");
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) throw new Error("invalid_amount");
  return Math.round(Number(normalized) * 100);
}

export function validateCloudPaymentsPay(event, pendingOrder) {
  const transactionId = String(event.TransactionId || "").trim();
  if (!transactionId) throw new Error("transaction_id_required");
  if (String(event.OperationType || "Payment") !== "Payment") throw new Error("operation_not_payment");
  if (String(event.InvoiceId || "") !== String(pendingOrder.invoiceId || "")) throw new Error("invoice_mismatch");
  if (String(event.AccountId || "") !== String(pendingOrder.accountId || "")) throw new Error("account_mismatch");
  if (String(event.Currency || "").toUpperCase() !== String(pendingOrder.currency || "RUB").toUpperCase()) throw new Error("currency_mismatch");
  if (moneyToMinor(event.Amount) !== Number(pendingOrder.amountMinor)) throw new Error("amount_mismatch");
  return {
    provider:"cloudpayments",
    eventType:"pay",
    transactionId,
    invoiceId:String(event.InvoiceId),
    accountId:String(event.AccountId),
    amountMinor:moneyToMinor(event.Amount),
    currency:String(event.Currency).toUpperCase(),
  };
}
