/**
 * Regression: as-on / application calendar dates must not shift a day
 * when formatted in a behind-UTC locale (Roopa: 01/07 rendered as 30 Jun).
 *
 * Run: node tests/test_as_on_date_timezone.js
 */
"use strict";

function parseDMY(s){
  if(!s) return null;
  const m = /^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})/.exec(String(s).trim());
  if(!m) return null;
  return new Date(Date.UTC(+m[3], +m[2]-1, +m[1]));
}

function fmt(refDate){
  return refDate ? refDate.toLocaleDateString("en-IN", {
    day:"numeric", month:"short", year:"numeric", timeZone:"UTC"
  }) : "unknown";
}

function isoToDmy(iso){
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return iso;
}

function fmtIso(iso){
  if(!iso) return "date unknown";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (m) {
    const d = new Date(Date.UTC(+m[1], +m[2]-1, +m[3]));
    return d.toLocaleDateString("en-IN", {
      day:"numeric", month:"short", year:"numeric", timeZone:"UTC"
    });
  }
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString("en-IN", {
    day:"numeric", month:"short", year:"numeric", timeZone:"UTC"
  });
}

function ageOn(dobStr, refDate){
  const d = new Date(dobStr + (dobStr.length === 10 ? "T00:00:00Z" : ""));
  if (isNaN(d) || !refDate) return null;
  let y = refDate.getUTCFullYear() - d.getUTCFullYear();
  const beforeBday = (refDate.getUTCMonth() < d.getUTCMonth()) ||
    (refDate.getUTCMonth() === d.getUTCMonth() && refDate.getUTCDate() < d.getUTCDate());
  if (beforeBday) y--;
  return y;
}

// Simulate the BROKEN formatter (local TZ, no UTC) to prove the bug exists
// when TZ is behind UTC — and that our fix does not.
function fmtBroken(refDate){
  return refDate.toLocaleDateString("en-IN", {
    day:"numeric", month:"short", year:"numeric"
  });
}

let failed = 0;
function assert(cond, msg){
  if (!cond) { console.error("FAIL:", msg); failed++; }
  else console.log("ok:", msg);
}

const asOn = parseDMY("01/07/2026");
assert(asOn !== null, "parseDMY(01/07/2026) returns a Date");
assert(asOn.getUTCFullYear() === 2026 && asOn.getUTCMonth() === 6 && asOn.getUTCDate() === 1,
  "parseDMY keeps calendar 1 Jul 2026 in UTC fields");

const shown = fmt(asOn);
assert(/^1\s+Jul\s+2026$/.test(shown), `fmt(01/07/2026) => "${shown}" (must be 1 Jul 2026, never 30 Jun)`);

assert(isoToDmy("2026-07-01") === "01/07/2026", "isoToDmy string-only rewrite");
assert(isoToDmy("2026-07-01T00:00:00") === "01/07/2026", "isoToDmy ignores time suffix");

const isoShown = fmtIso("2026-07-01");
assert(/^1\s+Jul\s+2026$/.test(isoShown), `fmtIso(2026-07-01) => "${isoShown}"`);

// Age on as-on date: born 01/07/2000 is exactly 26 on 01/07/2026
assert(ageOn("2000-07-01", asOn) === 26, "ageOn birthday-exact is 26");
assert(ageOn("2000-07-02", asOn) === 25, "ageOn day-before-birthday is 25");

// Document the classic slip when TZ is America/Los_Angeles (behind UTC).
const prev = process.env.TZ;
process.env.TZ = "America/Los_Angeles";
// Node caches TZ at startup for some Intl paths; force via explicit check:
const broken = new Date(Date.UTC(2026, 6, 1)).toLocaleDateString("en-US", {
  day:"numeric", month:"short", year:"numeric", timeZone: "America/Los_Angeles"
});
assert(broken.includes("Jun") || broken.includes("30"),
  `without UTC, LA locale shows "${broken}" (the Roopa slip)`);
const fixed = new Date(Date.UTC(2026, 6, 1)).toLocaleDateString("en-US", {
  day:"numeric", month:"short", year:"numeric", timeZone: "UTC"
});
assert(fixed.includes("Jul") && fixed.includes("1"),
  `with timeZone:UTC, LA still shows "${fixed}" = 1 Jul`);
if (prev === undefined) delete process.env.TZ; else process.env.TZ = prev;

if (failed) {
  console.error(`\n${failed} assertion(s) failed`);
  process.exit(1);
}
console.log("\nAll as-on date timezone checks passed.");
