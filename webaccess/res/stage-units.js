/*
  stage-units.js

  Feet-and-inches parsing and formatting for the 3D stage editor.
  Hand-written tokenizer + recursive-descent parser. No eval/Function.

  Accepted length literals: 16"  16 in  16 inch(es)  5'  5 ft  5 foot/feet
  5.5 ft  1' 16"  1 ft 16 in  1'16  3/4"  5' 3 1/2"  30 cm  1.2 m  450 mm
  A bare number (no unit) means inches.

  Expressions: + - * / and parentheses. * and / require a plain (unitless)
  number on at least one side. A leading operator (+2, -1', *2, /2) is
  relative to a "current value" (inches) passed in by the caller.

  All internal math is done in inches. Output is rounded to the nearest
  1/16" and normalised so inches never reach 12 (they carry into feet).
*/

const FEET_WORDS = ["ft", "foot", "feet"];
const INCH_WORDS = ["in", "inch", "inches"];
// exact inch-per-unit factors (1 inch = 2.54 cm exactly)
const METRIC_UNITS = { cm: 1 / 2.54, mm: 1 / 25.4, m: 100 / 2.54 };

// ---------------------------------------------------------------- lexer --

function tokenize(str) {
  const tokens = [];
  let i = 0;
  let sawSpace = false;

  while (i < str.length) {
    const c = str[i];
    if (c === " " || c === "\t") {
      sawSpace = true;
      i++;
      continue;
    }

    let tok;
    if (c >= "0" && c <= "9") {
      const m = /^\d+(\.\d+)?/.exec(str.slice(i));
      tok = { type: "NUM", value: parseFloat(m[0]), raw: m[0] };
      i += m[0].length;
    } else if (c === ".") {
      const m = /^\.\d+/.exec(str.slice(i));
      if (!m) throw new Error('invalid number near "' + str.slice(i) + '"');
      tok = { type: "NUM", value: parseFloat(m[0]), raw: m[0] };
      i += m[0].length;
    } else if (c === "'") {
      tok = { type: "QUOTE1", raw: c };
      i++;
    } else if (c === '"') {
      tok = { type: "QUOTE2", raw: c };
      i++;
    } else if (c === "+") {
      tok = { type: "PLUS", raw: c };
      i++;
    } else if (c === "-") {
      tok = { type: "MINUS", raw: c };
      i++;
    } else if (c === "*") {
      tok = { type: "STAR", raw: c };
      i++;
    } else if (c === "/") {
      tok = { type: "SLASH", raw: c };
      i++;
    } else if (c === "(") {
      tok = { type: "LPAREN", raw: c };
      i++;
    } else if (c === ")") {
      tok = { type: "RPAREN", raw: c };
      i++;
    } else if (/[A-Za-z]/.test(c)) {
      const m = /^[A-Za-z]+/.exec(str.slice(i));
      tok = { type: "WORD", value: m[0].toLowerCase(), raw: m[0] };
      i += m[0].length;
    } else {
      throw new Error('unexpected character "' + c + '"');
    }
    tok.spaceBefore = sawSpace;
    sawSpace = false;
    tokens.push(tok);
  }
  return tokens;
}

// ------------------------------------------------------------- parser ---

function peek(ctx, ahead) {
  return ctx.tokens[ctx.pos + (ahead || 0)];
}
function consume(ctx) {
  return ctx.tokens[ctx.pos++];
}
function atEnd(ctx) {
  return ctx.pos >= ctx.tokens.length;
}

// Reads NUM [ (space) NUM '/' NUM ] or NUM '/' NUM (fraction, no spaces
// around the slash) or a plain NUM. Returns a plain number.
function readNumberWithFraction(ctx) {
  const a = peek(ctx);
  if (!a || a.type !== "NUM")
    throw new Error('expected a number near "' + (a ? a.raw : "end of input") + '"');
  consume(ctx);

  // pure fraction: a/b, no whitespace around the slash
  const slash1 = peek(ctx);
  if (slash1 && slash1.type === "SLASH" && !slash1.spaceBefore) {
    const b = peek(ctx, 1);
    if (b && b.type === "NUM" && !b.spaceBefore) {
      consume(ctx); // slash
      consume(ctx); // b
      if (b.value === 0) throw new Error("division by zero in fraction");
      return a.value / b.value;
    }
  }

  // mixed number: a  b/c  (space before b, no space around the slash)
  const b2 = peek(ctx);
  if (b2 && b2.type === "NUM" && b2.spaceBefore) {
    const slash2 = peek(ctx, 1);
    if (slash2 && slash2.type === "SLASH" && !slash2.spaceBefore) {
      const c2 = peek(ctx, 2);
      if (c2 && c2.type === "NUM" && !c2.spaceBefore) {
        consume(ctx); // b2
        consume(ctx); // slash2
        consume(ctx); // c2
        if (c2.value === 0) throw new Error("division by zero in fraction");
        return a.value + b2.value / c2.value;
      }
    }
  }

  return a.value;
}

function isFeetMarker(tok) {
  return tok && (tok.type === "QUOTE1" || (tok.type === "WORD" && FEET_WORDS.indexOf(tok.value) !== -1));
}
function isInchMarker(tok) {
  return tok && (tok.type === "QUOTE2" || (tok.type === "WORD" && INCH_WORDS.indexOf(tok.value) !== -1));
}

// Parses one length literal. Returns { value (inches), hasUnit }.
function parseLengthTerm(ctx) {
  const startTok = peek(ctx);
  if (!startTok || startTok.type !== "NUM")
    throw new Error('expected a number near "' + (startTok ? startTok.raw : "end of input") + '"');

  const num = readNumberWithFraction(ctx);

  const unitTok = peek(ctx);
  if (unitTok && unitTok.type === "WORD" && Object.prototype.hasOwnProperty.call(METRIC_UNITS, unitTok.value)) {
    consume(ctx);
    return { value: num * METRIC_UNITS[unitTok.value], hasUnit: true };
  }

  if (isFeetMarker(unitTok)) {
    consume(ctx);
    let inches = 0;
    const nextTok = peek(ctx);
    if (nextTok && nextTok.type === "NUM") {
      inches = readNumberWithFraction(ctx);
      const inchUnit = peek(ctx);
      if (isInchMarker(inchUnit)) consume(ctx);
    }
    return { value: num * 12 + inches, hasUnit: true };
  }

  if (isInchMarker(unitTok)) {
    consume(ctx);
    return { value: num, hasUnit: true };
  }

  // bare number = inches
  return { value: num, hasUnit: false };
}

function parsePrimary(ctx) {
  const tok = peek(ctx);
  if (tok && tok.type === "LPAREN") {
    consume(ctx);
    const v = parseAddExpr(ctx);
    const close = peek(ctx);
    if (!close || close.type !== "RPAREN") throw new Error('expected ")"');
    consume(ctx);
    return v;
  }
  return parseLengthTerm(ctx);
}

function parseUnary(ctx) {
  const tok = peek(ctx);
  if (tok && tok.type === "PLUS") {
    consume(ctx);
    return parseUnary(ctx);
  }
  if (tok && tok.type === "MINUS") {
    consume(ctx);
    const v = parseUnary(ctx);
    return { value: -v.value, hasUnit: v.hasUnit };
  }
  return parsePrimary(ctx);
}

function parseMulExpr(ctx) {
  let left = parseUnary(ctx);
  for (;;) {
    const tok = peek(ctx);
    if (!tok || (tok.type !== "STAR" && tok.type !== "SLASH")) break;
    consume(ctx);
    const right = parseUnary(ctx);
    if (left.hasUnit && right.hasUnit)
      throw new Error('"' + tok.raw + '" needs a plain number on at least one side');
    if (tok.type === "SLASH" && right.value === 0) throw new Error("division by zero");
    const value = tok.type === "STAR" ? left.value * right.value : left.value / right.value;
    left = { value: value, hasUnit: left.hasUnit || right.hasUnit };
  }
  return left;
}

function parseAddExpr(ctx) {
  let left = parseMulExpr(ctx);
  for (;;) {
    const tok = peek(ctx);
    if (!tok || (tok.type !== "PLUS" && tok.type !== "MINUS")) break;
    consume(ctx);
    const right = parseMulExpr(ctx);
    const value = tok.type === "PLUS" ? left.value + right.value : left.value - right.value;
    left = { value: value, hasUnit: left.hasUnit || right.hasUnit };
  }
  return left;
}

/**
 * Parse a feet/inches length expression.
 * @param {string} text
 * @param {number} [currentInches] required only when text starts with a
 *        leading operator (relative change).
 * @returns {{ok:true, inches:number}|{ok:false, error:string}}
 */
export function parseLength(text, currentInches) {
  try {
    if (typeof text !== "string") return { ok: false, error: "no input" };
    const trimmed = text.trim();
    if (trimmed === "") return { ok: false, error: "empty input" };

    const tokens = tokenize(trimmed);
    if (tokens.length === 0) return { ok: false, error: "empty input" };

    const ctx = { tokens: tokens, pos: 0 };

    let relativeOp = null;
    const first = tokens[0];
    if (first.type === "PLUS" || first.type === "MINUS" || first.type === "STAR" || first.type === "SLASH") {
      relativeOp = first.type;
      ctx.pos = 1;
    }

    const result = parseAddExpr(ctx);
    if (!atEnd(ctx)) {
      const leftover = peek(ctx);
      return { ok: false, error: 'unexpected input near "' + leftover.raw + '"' };
    }

    let value;
    if (relativeOp) {
      if (currentInches === undefined || currentInches === null || isNaN(currentInches))
        return { ok: false, error: "no current value to apply a relative change to" };
      if (relativeOp === "PLUS") value = currentInches + result.value;
      else if (relativeOp === "MINUS") value = currentInches - result.value;
      else if (relativeOp === "STAR") value = currentInches * result.value;
      else {
        if (result.value === 0) return { ok: false, error: "division by zero" };
        value = currentInches / result.value;
      }
    } else {
      value = result.value;
    }

    if (!isFinite(value)) return { ok: false, error: "invalid result" };
    return { ok: true, inches: value };
  } catch (e) {
    return { ok: false, error: (e && e.message) || "invalid input" };
  }
}

function gcd(a, b) {
  a = Math.abs(a);
  b = Math.abs(b);
  while (b) {
    const t = b;
    b = a % b;
    a = t;
  }
  return a || 1;
}

// Formats a non-negative whole number of sixteenths as an inches string
// without the trailing double-quote, e.g. 152 -> "9 1/2", 64 -> "4".
function formatInchesSixteenths(sixteenths) {
  const whole = Math.floor(sixteenths / 16);
  const frac = sixteenths - whole * 16;
  if (frac === 0) return String(whole);
  const g = gcd(frac, 16);
  const num = frac / g;
  const den = 16 / g;
  return whole > 0 ? whole + " " + num + "/" + den : num + "/" + den;
}

/**
 * Format an inches value as a feet/inches string, rounded to 1/16".
 * @param {number} inches
 * @returns {string} e.g. "4' 0\"", "9 1/2\"", "-1' 3 1/2\""
 */
export function formatLength(inches) {
  if (!isFinite(inches)) return "0\"";
  const sixteenths = Math.round(inches * 16);
  const sign = sixteenths < 0 ? "-" : "";
  const abs16 = Math.abs(sixteenths);
  const FEET16 = 12 * 16;
  const feet = Math.floor(abs16 / FEET16);
  const remSixteenths = abs16 - feet * FEET16;
  const inchesPart = formatInchesSixteenths(remSixteenths);
  if (feet === 0) return sign + inchesPart + '"';
  return sign + feet + "' " + inchesPart + '"';
}

// ------------------------------------------------------------- selftest --

export function selftest() {
  const results = [];
  let pass = 0;
  let fail = 0;

  function checkParseFormat(name, text, currentInches, expected) {
    const r = parseLength(text, currentInches);
    let ok = false;
    let detail;
    if (!r.ok) {
      detail = "parse failed: " + r.error;
    } else {
      const formatted = formatLength(r.inches);
      ok = formatted === expected;
      detail = "inches=" + r.inches.toFixed(4) + " formatted=" + formatted + " expected=" + expected;
    }
    results.push({ name: name, ok: ok, detail: detail });
    if (ok) pass++;
    else fail++;
  }

  function checkFormat(name, inches, expected) {
    const formatted = formatLength(inches);
    const ok = formatted === expected;
    results.push({ name: name, ok: ok, detail: "formatted=" + formatted + " expected=" + expected });
    if (ok) pass++;
    else fail++;
  }

  function checkInvalid(name, text, currentInches) {
    const r = parseLength(text, currentInches);
    const ok = r.ok === false;
    results.push({ name: name, ok: ok, detail: ok ? "correctly rejected: " + r.error : "wrongly accepted as " + r.inches });
    if (ok) pass++;
    else fail++;
  }

  // basic units
  checkParseFormat('16"', '16"', undefined, "1' 4\"");
  checkParseFormat("16 in", "16 in", undefined, "1' 4\"");
  checkParseFormat("16 inches", "16 inches", undefined, "1' 4\"");
  checkParseFormat("16 inch", "16 inch", undefined, "1' 4\"");
  checkParseFormat("5'", "5'", undefined, "5' 0\"");
  checkParseFormat("5 ft", "5 ft", undefined, "5' 0\"");
  checkParseFormat("5 foot", "5 foot", undefined, "5' 0\"");
  checkParseFormat("5 feet", "5 feet", undefined, "5' 0\"");
  checkParseFormat("5.5 ft", "5.5 ft", undefined, "5' 6\"");
  checkParseFormat("1' 16\"", "1' 16\"", undefined, "2' 4\"");
  checkParseFormat("1 ft 16 in", "1 ft 16 in", undefined, "2' 4\"");
  checkParseFormat("1'16", "1'16", undefined, "2' 4\"");
  checkParseFormat('3/4"', '3/4"', undefined, '3/4"');
  checkParseFormat("5' 3 1/2\"", "5' 3 1/2\"", undefined, "5' 3 1/2\"");
  checkParseFormat("30 cm", "30 cm", undefined, "11 13/16\"");
  checkParseFormat("450 mm", "450 mm", undefined, "1' 5 11/16\"");
  checkParseFormat("bare number", "16", undefined, "1' 4\"");

  // 1.2 m: just check it parses to a sane value (~47.24")
  const m12 = parseLength("1.2 m", undefined);
  results.push({
    name: "1.2 m parses",
    ok: m12.ok && Math.abs(m12.inches - 47.244094488) < 0.01,
    detail: m12.ok ? "inches=" + m12.inches.toFixed(4) : "parse failed: " + m12.error,
  });
  if (m12.ok && Math.abs(m12.inches - 47.244094488) < 0.01) pass++;
  else fail++;

  // math and relative operators
  checkParseFormat("+2 on 2'4\"", "+2", 28, "2' 6\"");
  checkParseFormat("-1' on 5'", "-1'", 60, "4' 0\"");
  checkParseFormat("(10'-2')/2", "(10' - 2') / 2", undefined, "4' 0\"");
  checkParseFormat("*2 on 12", "*2", 12, "2' 0\"");
  checkParseFormat("/2 on 48", "/2", 48, "2' 0\"");

  // formatting-only examples from the spec
  checkFormat("format 0", 0, '0"');
  checkFormat("format 9.5", 9.5, "9 1/2\"");
  checkFormat("format -15.5", -15.5, "-1' 3 1/2\"");
  checkFormat("format 48", 48, "4' 0\"");
  checkFormat("format 16 (as inches)", 16, "1' 4\"");

  // invalid input
  checkInvalid("invalid: abc", "abc");
  checkInvalid("invalid: 2''3", "2''3");
  checkInvalid("invalid: 5 / 0", "5 / 0");
  checkInvalid("invalid: 1' * 2'", "1' * 2'");
  checkInvalid("invalid: leading op with no current", "+2", undefined);
  checkInvalid("invalid: empty", "");
  checkInvalid("invalid: unbalanced paren", "(5'");

  return { pass: pass, fail: fail, results: results };
}
