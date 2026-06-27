/**
 * Omerion CRM — Apps Script glue.
 * Responsibilities:
 *   1. Pull mirror tabs from Supabase every 5 min.
 *   2. Push founder edits in Contacts/Accounts/Opportunities → Supabase
 *      (via the local Omerion FastAPI so every change is logged).
 *   3. Approve / Reject buttons in `Review Queue` → `/hitl/resolve`
 *      on the local Omerion FastAPI (bearer auth).
 *   4. 06:30 daily digest email.
 *
 * Script Properties required:
 *   OMERION_BASE_URL       e.g. https://<ngrok-id>.ngrok.app (or prod URL)
 *   OMERION_TOKEN          bearer token matching OMERION_WEBHOOK_TOKEN
 *   FOUNDER_EMAIL           omerion.io@gmail.com
 *
 * Per-row Review Queue columns must include `review_id` and a signed
 * `token` column (one of approve_token / reject_token, populated by
 * the mirror pull). The buttons read those cells directly.
 */

const MIRROR_TABS = [
  { tab: 'Contacts',      endpoint: '/crm/contacts' },
  { tab: 'Accounts',      endpoint: '/crm/accounts' },
  { tab: 'Opportunities', endpoint: '/crm/opportunities' },
  { tab: 'Tasks',         endpoint: '/crm/build_tasks' },
  { tab: 'Review Queue',  endpoint: '/crm/review_queue?decision=pending' },
  { tab: 'Outreach Log',  endpoint: '/crm/outbound_communications?since=30d' },
  { tab: 'Deployments',   endpoint: '/crm/deployments' },
];

const EDITABLE_TABS = {
  Contacts:      { endpoint: '/crm/contacts',      pk: 'id' },
  Accounts:      { endpoint: '/crm/accounts',      pk: 'id' },
  Opportunities: { endpoint: '/crm/opportunities', pk: 'id' },
};

function _props() { return PropertiesService.getScriptProperties(); }
function _omerionFetch(path, options) {
  const base = _props().getProperty('OMERION_BASE_URL');
  const token = _props().getProperty('OMERION_TOKEN');
  const opts = Object.assign({
    method: 'get',
    muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
  }, options || {});
  return UrlFetchApp.fetch(base + path, opts);
}

function pullFromSupabase() {
  const ss = SpreadsheetApp.getActive();
  MIRROR_TABS.forEach(function(spec) {
    const resp = _omerionFetch(spec.endpoint);
    if (resp.getResponseCode() >= 300) {
      console.error('pull failed', spec.tab, resp.getContentText());
      return;
    }
    const payload = JSON.parse(resp.getContentText());
    const rows = payload.rows || [];
    const sheet = ss.getSheetByName(spec.tab);
    if (!sheet || rows.length === 0) return;
    const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    const values = rows.map(function(r) {
      return headers.map(function(h) { return h === 'synced_at' ? new Date().toISOString() : (r[h] ?? ''); });
    });
    sheet.getRange(2, 1, sheet.getMaxRows() - 1, headers.length).clearContent();
    sheet.getRange(2, 1, values.length, headers.length).setValues(values);
  });
}

function onEdit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  const spec = EDITABLE_TABS[sheet.getName()];
  if (!spec) return;
  const row = e.range.getRow();
  if (row === 1) return;
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const rowValues = sheet.getRange(row, 1, 1, headers.length).getValues()[0];
  const payload = {};
  headers.forEach(function(h, i) { if (h && h !== 'synced_at') payload[h] = rowValues[i]; });
  const id = payload[spec.pk];
  if (!id) return;
  _omerionFetch(spec.endpoint + '/' + id, {
    method: 'patch',
    payload: JSON.stringify({ patch: payload, source: 'sheets', editor: Session.getActiveUser().getEmail() }),
  });
}

function onApprove(reviewId) { return _resolveReview(reviewId, 'approved'); }
function onReject(reviewId)  { return _resolveReview(reviewId, 'rejected'); }

function _resolveReview(reviewId, decision) {
  const base = _props().getProperty('OMERION_BASE_URL');
  const token = _props().getProperty('OMERION_TOKEN');
  if (!base || !token) {
    SpreadsheetApp.getUi().alert('OMERION_BASE_URL / OMERION_TOKEN script properties are not set.');
    return;
  }
  const editor = Session.getActiveUser().getEmail();
  const reviewToken = _lookupReviewToken(reviewId, decision);
  if (!reviewToken) {
    SpreadsheetApp.getUi().alert('Could not locate signed token for review ' + reviewId);
    return;
  }
  const resp = UrlFetchApp.fetch(base.replace(/\/$/, '') + '/hitl/resolve', {
    method: 'post',
    contentType: 'application/json',
    muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + token },
    payload: JSON.stringify({
      review_id: reviewId,
      token: reviewToken,
      decision: decision,
      decided_by: editor,
    }),
  });
  if (resp.getResponseCode() >= 300) {
    SpreadsheetApp.getUi().alert('HITL resolve failed: ' + resp.getContentText());
    return;
  }
  SpreadsheetApp.getActive().toast(decision + ' sent for ' + reviewId, 'HITL', 3);
}

function _lookupReviewToken(reviewId, decision) {
  const sheet = SpreadsheetApp.getActive().getSheetByName('Review Queue');
  if (!sheet) return null;
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const idCol = headers.indexOf('review_id');
  const tokenCol = headers.indexOf(decision === 'approved' ? 'approve_token' : 'reject_token');
  if (idCol < 0 || tokenCol < 0) return null;
  const data = sheet.getRange(2, 1, sheet.getLastRow() - 1, headers.length).getValues();
  for (const row of data) {
    if (row[idCol] === reviewId) return row[tokenCol];
  }
  return null;
}

function sendDigestEmail() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName('Daily Digest');
  if (!sheet) return;
  const range = sheet.getDataRange();
  const html = _rangeToHtml(range);
  GmailApp.sendEmail(
    _props().getProperty('FOUNDER_EMAIL'),
    'Omerion — Daily Digest ' + Utilities.formatDate(new Date(), 'America/Toronto', 'yyyy-MM-dd'),
    'HTML only — see inline.',
    { htmlBody: html }
  );
}

function _rangeToHtml(range) {
  const values = range.getValues();
  const rows = values.map(function(r) { return '<tr>' + r.map(function(c) { return '<td style="padding:4px 8px;border-bottom:1px solid #eee">' + (c === '' ? '&nbsp;' : c) + '</td>'; }).join('') + '</tr>'; }).join('');
  return '<table style="border-collapse:collapse;font-family:Inter,Arial,sans-serif;font-size:13px">' + rows + '</table>';
}

function installTriggers() {
  ScriptApp.getProjectTriggers().forEach(function(t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('pullFromSupabase').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('sendDigestEmail').timeBased().atHour(6).nearMinute(30).everyDays(1).create();
}
