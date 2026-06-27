/**
 * OMERION BACKBONE ENGINE
 * Core deterministic automation for the OMERION Command Center.
 * Paste this into Google Apps Script (Extensions → Apps Script) in the 
 * Omerion Command Center Google Sheet.
 * 
 * Built by: Antigravity
 * Design principle: 80% deterministic code / 20% AI
 */

// =============================================================================
// CONFIGURATION — Sheet Names & Column Indices
// =============================================================================

const CONFIG = {
  sheets: {
    contacts: 'Contacts',
    accounts: 'Accounts',
    opportunities: 'Opportunities',
    tasks: 'Tasks',
    reviewQueue: 'Review Queue',
    outreachLog: 'Outreach Log',
    deployments: 'Deployments',
    dailyDigest: 'Daily Digest'
  },
  timezone: 'America/Toronto'
};

// =============================================================================
// A1: ENUM VALIDATION LAYER
// =============================================================================

const ENUMS = {
  persona: [
    'Real Estate Investor',
    'Real Estate Team Lead',
    'High-Volume Independent Agent',
    'Wholesaler / Flipper',
    'Property Management Company',
    'Brokerage Owner',
    'Real Estate Transaction Attorney',
    'Real Estate Business Attorney',
    'Real Estate Wholesaler (Institutional)',
    'NEEDS_REVIEW'
  ],

  companyType: [
    'Residential Brokerage (Independent)',
    'Luxury Real Estate Brokerage',
    'Commercial Real Estate Brokerage',
    'Real Estate Franchise',
    'Real Estate Team (Internal Hire)',
    'Boutique Brokerage (5-20 Agents)',
    'Real Estate Investment Firm',
    'Private Equity Real Estate Fund',
    'Real Estate Development Company',
    'Multi-Family Investment Group',
    'REIT',
    'Fix & Flip / Renovation Company',
    'Land Acquisition Firm',
    'PropTech Startup',
    'Real Estate SaaS Company',
    'CRM Platform (Real Estate Vertical)',
    'Real Estate Data & Analytics Company',
    'Real Estate Marketing Technology Firm',
    'Property Management Company',
    'Short-Term Rental Management Company',
    'Real Estate Coaching & Training Company',
    'HOA Management Firm',
    'Commercial Property Management Firm',
    'NEEDS_REVIEW'
  ],

  contactStatus: [
    'New',
    'Active',
    'Sequence Complete',
    'Opted Out',
    'Converted',
    'NEEDS_REVIEW'
  ],

  dealStage: [
    'Discovery',
    'Proposal',
    'Negotiation',
    'Closed Won',
    'Closed Lost'
  ],

  taskType: [
    'Draft Initial Outreach',
    'Draft Follow-Up',
    'Schedule Call',
    'Manual Review',
    'Research Company'
  ],

  taskStatus: ['Open', 'In Progress', 'Draft Complete', 'Complete', 'Skipped'],
  reviewStatus: ['Pending Approval', 'Approved', 'Rejected', 'Send Failed'],
  channel: ['Email', 'LinkedIn']
};

/**
 * Validates a value against an enum list.
 * @param {string} value - The value to validate
 * @param {string} enumName - Key in the ENUMS object
 * @returns {Object} {valid: boolean, value: string, error: string|null}
 */
function validateEnum(value, enumName) {
  if (!ENUMS[enumName]) {
    return { valid: false, value: value, error: 'Unknown enum: ' + enumName };
  }
  if (!value || value.toString().trim() === '') {
    return { valid: false, value: '', error: enumName + ' is empty/null' };
  }
  const trimmed = value.toString().trim();
  const isValid = ENUMS[enumName].indexOf(trimmed) !== -1;
  return {
    valid: isValid,
    value: trimmed,
    error: isValid ? null : '"' + trimmed + '" is not a valid ' + enumName
  };
}

/**
 * Validates multiple fields at once. Returns all errors.
 * @param {Object} fields - {enumName: value, ...}
 * @returns {Object} {valid: boolean, errors: string[]}
 */
function validateFields(fields) {
  var errors = [];
  for (var enumName in fields) {
    var result = validateEnum(fields[enumName], enumName);
    if (!result.valid) errors.push(result.error);
  }
  return { valid: errors.length === 0, errors: errors };
}

// =============================================================================
// A2: OPT-OUT GUARD CLAUSE LIBRARY
// =============================================================================

/**
 * Checks if a contact is opted out. MUST be called before any action on a contact.
 * @param {string} contactId - The contact ID to check
 * @returns {boolean} true if opted out (BLOCK the action)
 */
function isOptedOut(contactId) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CONFIG.sheets.contacts);
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var idCol = headers.indexOf('id');
  var statusCol = headers.indexOf('status');
  if (idCol === -1 || statusCol === -1) return true; // fail-safe: block if columns not found
  for (var i = 1; i < data.length; i++) {
    if (data[i][idCol] == contactId) {
      return data[i][statusCol] === 'Opted Out';
    }
  }
  return true; // contact not found — fail-safe: block
}

/**
 * Sets a contact to Opted Out and cancels all their open tasks.
 * @param {string} contactId - The contact to opt out
 * @param {string} reason - Why they were opted out
 */
function setOptedOut(contactId, reason) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // Update contact status
  var contactSheet = ss.getSheetByName(CONFIG.sheets.contacts);
  var contactData = contactSheet.getDataRange().getValues();
  var headers = contactData[0];
  var idCol = headers.indexOf('id');
  var statusCol = headers.indexOf('status');
  var scoreCol = headers.indexOf('intent_score');

  for (var i = 1; i < contactData.length; i++) {
    if (contactData[i][idCol] == contactId) {
      contactSheet.getRange(i + 1, statusCol + 1).setValue('Opted Out');
      contactSheet.getRange(i + 1, scoreCol + 1).setValue(0);
      break;
    }
  }

  // Cancel all open tasks for this contact
  cancelOpenTasks(contactId, 'Contact opted out: ' + (reason || 'No reason'));

  // Remove any pending Review Queue items
  var rqSheet = ss.getSheetByName(CONFIG.sheets.reviewQueue);
  var rqData = rqSheet.getDataRange().getValues();
  var rqHeaders = rqData[0];
  var rqContactCol = rqHeaders.indexOf('contact_id');
  var rqStatusCol = rqHeaders.indexOf('status');

  for (var j = 1; j < rqData.length; j++) {
    if (rqData[j][rqContactCol] == contactId && rqData[j][rqStatusCol] === 'Pending Approval') {
      rqSheet.getRange(j + 1, rqStatusCol + 1).setValue('Rejected');
    }
  }
}

/**
 * Cancels all open tasks for a contact.
 */
function cancelOpenTasks(contactId, reason) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CONFIG.sheets.tasks);
  var data = sheet.getDataRange().getValues();
  var headers = data[0];
  var contactCol = headers.indexOf('contact_id');
  var statusCol = headers.indexOf('status');
  var descCol = headers.indexOf('description');

  for (var i = 1; i < data.length; i++) {
    if (data[i][contactCol] == contactId && (data[i][statusCol] === 'Open' || data[i][statusCol] === 'In Progress')) {
      sheet.getRange(i + 1, statusCol + 1).setValue('Skipped');
      var existing = data[i][descCol] || '';
      sheet.getRange(i + 1, descCol + 1).setValue(existing + ' [CANCELLED: ' + reason + ']');
    }
  }
}

// =============================================================================
// A3: FIT SCORE DETERMINISTIC CALCULATOR
// =============================================================================

/**
 * Calculates fit_score from 5 deterministic lookup tables.
 * @param {Object} data - {headCount, techStack, industry, title, location}
 * @returns {number} 0-100 fit score
 */
function calculateFitScore(data) {
  var companySize = scoreCompanySize(data.headCount);
  var techMaturity = scoreTechMaturity(data.techStack);
  var industryMatch = scoreIndustryMatch(data.industry);
  var titleSeniority = scoreTitleSeniority(data.title);
  var geoFit = scoreGeographicFit(data.location);

  return Math.round(
    (companySize * 0.25) +
    (techMaturity * 0.20) +
    (industryMatch * 0.20) +
    (titleSeniority * 0.20) +
    (geoFit * 0.15)
  );
}

function scoreCompanySize(headCount) {
  if (!headCount || headCount === 'Unknown') return 50;
  var n = parseInt(headCount);
  if (isNaN(n)) return 50;
  if (n <= 5) return 40;
  if (n <= 20) return 70;
  if (n <= 50) return 90;
  return 100;
}

function scoreTechMaturity(techStack) {
  if (!techStack || techStack === 'Unknown') return 65;
  var stack = techStack.toString().toLowerCase();
  var modernTools = ['salesforce', 'hubspot', 'follow up boss', 'kvcore', 'lofty', 'chime', 'sierra', 'ylopo'];
  var hasModern = modernTools.some(function(tool) { return stack.indexOf(tool) !== -1; });
  if (hasModern) return 30; // already has tech = lower need
  if (stack.indexOf('spreadsheet') !== -1 || stack.indexOf('manual') !== -1) return 100;
  return 65;
}

function scoreIndustryMatch(industry) {
  if (!industry || industry === 'Unknown') return 50;
  var ind = industry.toString().toLowerCase();
  var directRE = ['real estate', 'brokerage', 'property', 'realty', 'housing', 'mortgage'];
  var adjacent = ['legal', 'title', 'escrow', 'construction', 'development', 'insurance'];
  if (directRE.some(function(k) { return ind.indexOf(k) !== -1; })) return 100;
  if (adjacent.some(function(k) { return ind.indexOf(k) !== -1; })) return 60;
  return 20;
}

function scoreTitleSeniority(title) {
  if (!title || title === 'Unknown') return 50;
  var t = title.toString().toLowerCase();
  var cSuite = ['owner', 'ceo', 'coo', 'cto', 'founder', 'principal', 'managing broker', 'broker/owner'];
  var director = ['director', 'vp', 'vice president', 'head of', 'chief'];
  var manager = ['manager', 'team lead', 'supervisor', 'coordinator'];
  if (cSuite.some(function(k) { return t.indexOf(k) !== -1; })) return 100;
  if (director.some(function(k) { return t.indexOf(k) !== -1; })) return 80;
  if (manager.some(function(k) { return t.indexOf(k) !== -1; })) return 60;
  return 30;
}

function scoreGeographicFit(location) {
  if (!location || location === 'Unknown') return 50;
  var loc = location.toString().toLowerCase();
  if (loc.indexOf('united states') !== -1 || loc.indexOf('usa') !== -1 ||
      /\b(ca|ny|tx|fl|il|az|nv|co|ga|nc|oh|pa|wa|or|ma|va|md|nj|ct|tn|mi|mn|wi|mo|in|sc)\b/.test(loc)) return 100;
  if (loc.indexOf('canada') !== -1 || loc.indexOf('ontario') !== -1 || 
      loc.indexOf('toronto') !== -1 || loc.indexOf('vancouver') !== -1) return 80;
  if (loc.indexOf('uk') !== -1 || loc.indexOf('australia') !== -1) return 50;
  return 20;
}

// =============================================================================
// A4: LEAD INGESTION + DEDUP
// =============================================================================

/**
 * Ingests a lead. Upserts by linkedin_url. Respects Opted Out status.
 * @param {Object} lead - {firstName, lastName, title, company, linkedinUrl, email, location, source}
 * @returns {Object} {action: 'created'|'updated'|'skipped', contactId: string, reason: string}
 */
function ingestLead(lead) {
  if (!lead.linkedinUrl && !lead.email) {
    return { action: 'skipped', contactId: null, reason: 'No linkedin_url or email — cannot dedup' };
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(CONFIG.sheets.contacts);
  var data = sheet.getDataRange().getValues();
  var headers = data[0];

  var idCol = headers.indexOf('id');
  var linkedinCol = headers.indexOf('linkedin_url');
  var emailCol = headers.indexOf('email');
  var statusCol = headers.indexOf('status');

  // Check for existing contact by linkedin_url or email
  for (var i = 1; i < data.length; i++) {
    var existingLinkedin = data[i][linkedinCol] ? data[i][linkedinCol].toString().trim().toLowerCase() : '';
    var existingEmail = data[i][emailCol] ? data[i][emailCol].toString().trim().toLowerCase() : '';
    var inputLinkedin = lead.linkedinUrl ? lead.linkedinUrl.toString().trim().toLowerCase() : '';
    var inputEmail = lead.email ? lead.email.toString().trim().toLowerCase() : '';

    if ((inputLinkedin && existingLinkedin === inputLinkedin) ||
        (inputEmail && existingEmail === inputEmail)) {
      // FOUND — check opt-out before updating
      if (data[i][statusCol] === 'Opted Out') {
        return { action: 'skipped', contactId: data[i][idCol], reason: 'Contact is Opted Out — will not re-ingest' };
      }
      // Update non-empty fields only (don't overwrite good data with blanks)
      return { action: 'updated', contactId: data[i][idCol], reason: 'Existing contact updated' };
    }
  }

  // NEW — create contact
  var newId = 'C-' + Utilities.getUuid().substring(0, 8);
  var newRow = [];
  headers.forEach(function(h) {
    switch (h) {
      case 'id': newRow.push(newId); break;
      case 'first_name': newRow.push(lead.firstName || ''); break;
      case 'last_name': newRow.push(lead.lastName || ''); break;
      case 'email': newRow.push(lead.email || ''); break;
      case 'linkedin_url': newRow.push(lead.linkedinUrl || ''); break;
      case 'persona': newRow.push(''); break; // Scout will classify
      case 'fit_score': newRow.push(0); break;
      case 'intent_score': newRow.push(0); break;
      case 'source': newRow.push(lead.source || 'LinkedIn'); break;
      case 'status': newRow.push('New'); break;
      case 'created_at': newRow.push(new Date().toISOString()); break;
      default: newRow.push(''); break;
    }
  });

  sheet.appendRow(newRow);
  return { action: 'created', contactId: newId, reason: 'New contact created' };
}

// =============================================================================
// A5: TASK GENERATION RULES ENGINE
// =============================================================================

/**
 * Creates a task if one doesn't already exist for the same contact+type.
 * Checks opt-out and sequence status before creating.
 * @param {string} contactId
 * @param {string} taskType - Must be valid enum
 * @param {string} description
 * @returns {Object} {created: boolean, reason: string}
 */
function generateTask(contactId, taskType, description) {
  // Validate task type
  var typeCheck = validateEnum(taskType, 'taskType');
  if (!typeCheck.valid) return { created: false, reason: typeCheck.error };

  // Opt-out guard
  if (isOptedOut(contactId)) {
    return { created: false, reason: 'Contact is Opted Out — task blocked' };
  }

  // Dedup: check for existing open task of same type
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var taskSheet = ss.getSheetByName(CONFIG.sheets.tasks);
  var taskData = taskSheet.getDataRange().getValues();
  var taskHeaders = taskData[0];
  var tContactCol = taskHeaders.indexOf('contact_id');
  var tTypeCol = taskHeaders.indexOf('type');
  var tStatusCol = taskHeaders.indexOf('status');

  for (var i = 1; i < taskData.length; i++) {
    if (taskData[i][tContactCol] == contactId &&
        taskData[i][tTypeCol] === taskType &&
        (taskData[i][tStatusCol] === 'Open' || taskData[i][tStatusCol] === 'In Progress')) {
      return { created: false, reason: 'Duplicate: open task of same type already exists' };
    }
  }

  // Sequence check for outreach tasks
  if (taskType === 'Draft Initial Outreach' || taskType === 'Draft Follow-Up') {
    var seqPos = getSequencePosition(contactId, 'Email');
    var seqPosLi = getSequencePosition(contactId, 'LinkedIn');
    if (seqPos >= 4 && seqPosLi >= 4) {
      return { created: false, reason: 'Sequence Complete on all channels — no more outreach' };
    }
  }

  // Create the task
  var newId = 'T-' + Utilities.getUuid().substring(0, 8);
  var dueDate = new Date();
  dueDate.setDate(dueDate.getDate() + 1); // due tomorrow by default

  var newRow = [];
  taskHeaders.forEach(function(h) {
    switch (h) {
      case 'id': newRow.push(newId); break;
      case 'type': newRow.push(taskType); break;
      case 'assigned_to': newRow.push('System'); break;
      case 'contact_id': newRow.push(contactId); break;
      case 'due_date': newRow.push(dueDate.toISOString()); break;
      case 'status': newRow.push('Open'); break;
      case 'description': newRow.push(description || ''); break;
      default: newRow.push(''); break;
    }
  });

  taskSheet.appendRow(newRow);
  return { created: true, reason: 'Task created: ' + newId };
}

// =============================================================================
// A6: SEQUENCE COUNTER
// =============================================================================

/**
 * Counts outreach touches for a contact on a specific channel.
 * Includes BOTH sent messages (Outreach Log) AND pending drafts (Review Queue).
 * @param {string} contactId
 * @param {string} channel - 'Email' or 'LinkedIn'
 * @returns {number} Total touch count (sent + pending)
 */
function getSequencePosition(contactId, channel) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sentCount = 0;
  var pendingCount = 0;

  // Count sent messages in Outreach Log
  var olSheet = ss.getSheetByName(CONFIG.sheets.outreachLog);
  if (olSheet) {
    var olData = olSheet.getDataRange().getValues();
    var olHeaders = olData[0];
    var olContactCol = olHeaders.indexOf('contact_id');
    var olChannelCol = olHeaders.indexOf('channel');
    for (var i = 1; i < olData.length; i++) {
      if (olData[i][olContactCol] == contactId && olData[i][olChannelCol] === channel) {
        sentCount++;
      }
    }
  }

  // Count pending in Review Queue
  var rqSheet = ss.getSheetByName(CONFIG.sheets.reviewQueue);
  if (rqSheet) {
    var rqData = rqSheet.getDataRange().getValues();
    var rqHeaders = rqData[0];
    var rqContactCol = rqHeaders.indexOf('contact_id');
    var rqChannelCol = rqHeaders.indexOf('channel');
    var rqStatusCol = rqHeaders.indexOf('status');
    for (var j = 1; j < rqData.length; j++) {
      if (rqData[j][rqContactCol] == contactId &&
          rqData[j][rqChannelCol] === channel &&
          rqData[j][rqStatusCol] === 'Pending Approval') {
        pendingCount++;
      }
    }
  }

  return sentCount + pendingCount;
}

/**
 * Checks if a contact has completed their sequence on a channel.
 * @returns {boolean} true if 4+ touches (sent + pending)
 */
function isSequenceComplete(contactId, channel) {
  return getSequencePosition(contactId, channel) >= 4;
}

// =============================================================================
// A7: TEMPLATE SELECTION
// =============================================================================

/**
 * Template column index mapping — persona to column position.
 * These indices match the column order in the Outreach Templates sheet.
 */
const PERSONA_COLUMN_MAP = {
  'Real Estate Investor': 0,            // Column A
  'Real Estate Team Lead': 1,           // Column B
  'High-Volume Independent Agent': 2,   // Column C
  'Wholesaler / Flipper': 3,            // Column D
  'Property Management Company': 4,     // Column E
  'Brokerage Owner': 5,                 // Column F
  'Real Estate Transaction Attorney': 6,// Column G
  'Real Estate Business Attorney': 7,   // Column H
  'Real Estate Wholesaler (Institutional)': 8 // Column I
};

/**
 * Selects the correct template based on persona, channel, and sequence step.
 * @param {string} persona - One of 9 valid persona values
 * @param {string} channel - 'Email' or 'LinkedIn'
 * @param {number} sequenceStep - 1, 2, 3, or 4
 * @param {string|null} jobPostingUrl - If exists, use "Replace the Hire" strategy
 * @param {string|null} companyType - Required if jobPostingUrl exists
 * @returns {Object} {strategy, subject, bodyRow, columnIndex, error}
 */
function selectTemplate(persona, channel, sequenceStep, jobPostingUrl, companyType) {
  // Determine strategy
  var strategy = (jobPostingUrl && jobPostingUrl.trim() !== '') ? 'replace_the_hire' : 'standard';

  if (strategy === 'standard') {
    var personaCheck = validateEnum(persona, 'persona');
    if (!personaCheck.valid || persona === 'NEEDS_REVIEW') {
      return { strategy: strategy, error: 'Invalid persona for template selection: ' + persona };
    }
    var colIdx = PERSONA_COLUMN_MAP[persona];
    if (colIdx === undefined) {
      return { strategy: strategy, error: 'No template column mapped for persona: ' + persona };
    }

    if (channel === 'Email') {
      return {
        strategy: 'standard_email',
        subjectTab: 'Copy of Outreach Templates - Subject',
        bodyTab: 'Outreach Templates - Body',
        columnIndex: colIdx,
        row: sequenceStep + 1, // row 1 = header, row 2+ = templates
        error: null
      };
    } else if (channel === 'LinkedIn') {
      return {
        strategy: 'standard_linkedin',
        bodyTab: 'LinkedIn - Outreach Templates',
        columnIndex: colIdx,
        row: sequenceStep + 1,
        error: null
      };
    }
  } else {
    // Replace the Hire — use company type columns
    if (!companyType) {
      return { strategy: strategy, error: 'company_type required for Replace the Hire strategy' };
    }
    var ctCheck = validateEnum(companyType, 'companyType');
    if (!ctCheck.valid || companyType === 'NEEDS_REVIEW') {
      return { strategy: strategy, error: 'Invalid company_type: ' + companyType };
    }
    // Company type column index would need to be looked up dynamically
    // from the Job Postings sheet headers since there are 25+ columns
    return {
      strategy: 'replace_the_hire',
      subjectTab: 'Email Subject Copy Templates - Job Postings',
      bodyTab: 'Email Copy Templates - Job Postings',
      companyType: companyType,
      lookupByHeader: true, // signal to match companyType against header row
      error: null
    };
  }

  return { strategy: strategy, error: 'Unknown channel: ' + channel };
}

// =============================================================================
// UTILITY: Generate UUID (for Google Apps Script compatibility)
// =============================================================================

if (typeof Utilities === 'undefined') {
  // Fallback for testing outside Google Apps Script
  var Utilities = {
    getUuid: function() {
      return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      });
    }
  };
}
