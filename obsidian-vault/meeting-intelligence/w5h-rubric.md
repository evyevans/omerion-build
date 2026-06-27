# W5H Extraction Rubric

## W5H Extraction Rules

Extract the following 5 fields from the meeting transcript. All fields are strings except `who`. Return JSON matching the W5H model exactly.

### who (list[str])
All named stakeholders present or discussed. Include titles where mentioned. Minimum: the primary contact. Maximum: 10 entries.

Example: `["Sarah Kim (CFO)", "Operations team (unnamed, ~12 people)", "Mark (IT, mentioned absent)"]`

If no names are stated: `["Prospect (unnamed)"]`

### what
The core operational problem stated or implied. Must be ≥1 sentence. Do not invent or extrapolate beyond what was stated.

If not stated: output exactly `"Unstated — see transcript context."`

### where
Deployment context — company name, department, geography, or platform. Include all that are mentioned.

Example: `"Mid-market SaaS, Revenue Operations team (~8 reps), North America, uses HubSpot."`

If not stated: `"Context not disclosed."`

### when
Any deadline, urgency signal, or timeline stated. Include both absolute dates and relative signals ("Q3", "before the board meeting", "ASAP").

If not stated: output exactly `"No explicit timeline stated."`

### how_much
Budget signal, deal size indicator, or revenue impact discussed. Include ranges if stated. Never infer — only report what was explicitly said.

If not stated: output exactly `"Budget not disclosed."`

## Quality Floor

- **Minimum transcript length:** 500 words of substantive dialogue (exclude greetings, filler, scheduling talk)
- If transcript is below floor: set `low_meeting_engagement` flag in the flag-raising step
- **Never fabricate** W5H fields — "unknown" and "not stated" are valid and preferred over invention
- **Transcript truncation:** Input is capped at 24,000 characters (word-boundary truncation). If transcript is truncated, append `" [TRUNCATED]"` to the `what` field so downstream nodes know context may be incomplete.

## JSON Output Format

```json
{
  "who": ["Name (Title)", "..."],
  "what": "string",
  "where": "string",
  "when": "string",
  "how_much": "string"
}
```

No additional fields. No explanatory prose outside the JSON block.
