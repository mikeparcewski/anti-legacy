# Java idiom → rule reference (Tier 3)

Load this when the node under crawl is `language: java` (or its file ends `.java`) and you
need to turn a method body into business rules. This is a **lookup table from Java idioms to
rule shapes**, not a procedure. You already have the loop (extract_rule) and the writing
standard (`reference/writing-standard.md`); this tells you what each Java construct *means* as
a rule and where the traps are.

Ground truth is the **method body** (`wicked_estate source <name>`), never the Javadoc, the
`@deprecated` tag, or the test. Comments are CLAIMS to confirm against code (guardrail b).
Name terms with **confirmed + trusted_verified** vocabulary; PROPOSE an unknown token, never
coin it inline (guardrail c).

All examples below are read verbatim from the real graph
`.anti-legacy/graphs/credit-card-java.db` (223 nodes, 39 files: a credit-card type/validation
estate — a `CreditCardFactory`, card subclasses, and a reader/writer Strategy stack). Kinds in
this graph: `method` 78, `class` 34, `field` 16, `interface` 3, `struct` 6, plus `file`/`import`/`module`.

---

## The idiom → rule map

| Java construct | What it is as a rule | Rule kind | Watch for |
|---|---|---|---|
| Method with side effect / return value | one capability of the class | `RULE-###` | private helpers are still rules if they encode logic |
| `if`/`else if` chain returning distinct types/values | **one rule per branch** | `RULE-###` each | order matters — earlier branches pre-empt later ones (see worked example) |
| `switch` / `case` (or `switch`-expression `->`) | one rule per `case`, plus `default` | `RULE-###` each | fall-through (no `break`) merges cases into one rule |
| Guard clause: `if (x == null \|\| x.isEmpty()) ...` | input validation rule | `VAL-###` | null **and** empty are usually two predicates in one guard — capture both |
| `length`, `charAt`, numeric/range compare, regex match | validation predicate with **literal constants** | `VAL-###` | constants are parity-critical — pin every literal (==13, >19, '4') |
| `throw new …Exception(...)` | an error path | `ERR-###` | the message string is the business meaning; capture it |
| `try/catch` swallowing the exception (`catch(Exception e){ e.printStackTrace(); }`) | a **silent failure** path — flag it | `ERR-###` + RISK | swallowed errors return partial/empty results; this is a behavior, not a bug to fix |
| `interface` + `implements` (Strategy/Factory/template) | the interface is the **contract**; each impl is a variant rule | `RULE-###` per impl | the polymorphic dispatch point is itself a rule (which impl is chosen, and why) |
| `@Override` | this impl realizes a contract method | (annotation on the impl's rule) | confirms the interface↔impl edge; not a rule by itself |
| BigDecimal / `*` / `/` / `%` on money, rates, counts | numeric output | `RULE-###` + **parity_rule** | `double`/`float` rounding ≠ COBOL COMP-3 — see Numeric section |
| Stream pipeline (`.stream().filter().map().collect()`) | a transform/aggregate rule | `RULE-###` | the terminal op (`collect`/`reduce`/`count`/`anyMatch`) is the output semantics |
| `Optional<T>` / `orElse` / `orElseThrow` | presence rule + its fallback/error | `VAL-###` or `ERR-###` | `orElseThrow` is an `ERR-###`; `orElse(default)` is a default-value `RULE-###` |
| Collection mutation in a loop (`list.add` in `while`) | accumulation rule | `RULE-###` | the loop's **termination condition** is part of the rule |
| `enum` constants | a closed value domain (an entity) | (vocabulary entity) + `VAL-###` if used to gate | propose the enum name as an entity term with its members |

### If a framework is present (Spring / JPA / Jakarta validation)

This estate is plain Java (no Spring/JPA — only `java.*` imports), so the rows below are the
general map; confirm against the real imports of the node you're crawling, don't assume them.

| Annotation | Rule meaning | Rule kind |
|---|---|---|
| `@NotNull` / `@NotBlank` / `@Size(min,max)` / `@Pattern` | declarative validation — one rule per annotation, constants from the args | `VAL-###` |
| `@Min` / `@Max` / `@DecimalMin` / `@Digits` | numeric bound → parity-relevant | `VAL-###` + parity if it gates money |
| `@Entity` + `@Table` / `@Column` | the class is a domain entity; columns are its fields | (vocabulary entity) |
| `@Transactional` | the method is one atomic unit; partial failure rolls back | `RULE-###` (atomicity) + `ERR-###` on rollback path |
| `@GetMapping` / `@PostMapping` / `@RequestMapping` | an entry point; the handler body holds the rules | crawl the body, the mapping is provenance |
| `@Service` / `@Repository` / `@Component` | role marker — tells you where logic vs persistence lives | (no rule; routing hint) |

Confirm an annotation's effect against the **method body** when one exists. An annotation with
custom logic behind it (a custom validator) is a CLAIM until you read that validator.

---

## Numeric outputs (guardrail e — mandatory parity)

Any method whose output is money, a rate, a percentage, a count, or a validated length/range
**must** carry a `parity_rule`. Java's traps differ from COBOL's:

- `double`/`float` are binary floating point — `0.1 + 0.2 != 0.3`. If the legacy was COBOL
  COMP-3 (fixed decimal), a Java `double` port silently diverges. Flag the type and pin the
  expected scale.
- `int` division truncates (`7/2 == 3`); `BigDecimal` needs an explicit `RoundingMode` and
  `scale`. Capture both in the parity_rule.
- `String`-based numeric checks (this estate's `cardNumber.length()`, `charAt`, regex
  `[0-9]+`) carry **exact integer constants** — these ARE the parity rule. Pin every literal.

Example from this graph — `CreditCardFactory/isAmExCC()`:
```java
return cardNumber.length() == 15 && cardNumber.charAt(0) == '3'
    && (cardNumber.charAt(1) == '4' || cardNumber.charAt(1) == '7');
```
→ `VAL-###` "AmEx number is exactly 15 digits, first digit 3, second digit 4 or 7" with a
parity_rule pinning `length == 15`, `[0]=='3'`, `[1] in {'4','7'}`. Drop any literal and the
rule is unfalsifiable.

---

## Worked example — `CreditCardFactory.createCreditCard(CreditCardEntry)`

Real node: SymbolId
`ts-java . . . src/main/java/com/cmpe202/individualproject/main/CreditCardFactory/createCreditCard().`
(kind `method`), file `src/main/java/com/cmpe202/individualproject/main/CreditCardFactory.java`.
Body read verbatim via `wicked_estate source createCreditCard`:

```java
public static CreditCardEntry createCreditCard(CreditCardEntry creditCardRecord) {
    String cardNumber = creditCardRecord.getCardNumber();
    String expirationDate = creditCardRecord.getExpirationDate();
    String cardHolderName = creditCardRecord.getCardHolderName();

    if(isNullOrEmpty(cardNumber))
        return new InvalidCC("Invalid: empty/null card number", ...);
    else if(exceeds19Digit(cardNumber))
        return new InvalidCC("Invalid: more than 19 digits", ...);
    else if(hasAplhabets(cardNumber))
        return new InvalidCC("Invalid: non numeric characters", ...);
    else if (isVisaCC(cardNumber))    return new VisaCC("Visa", ...);
    else if (isMasterCC(cardNumber))  return new MasterCC("MasterCard", ...);
    else if (isAmExCC(cardNumber))    return new AmExCC("AmericanExpress", ...);
    else if (isDiscoverCC(cardNumber))return new DiscoverCC("Discover", ...);
    else                              return new InvalidCC("Invalid: Not a possible card number", ...);
}
```

This **one method** decomposes into multiple rules (M:N — guardrail d; see
`reference/decomposition.md`). The `else if` chain is ordered, so each branch's rule carries an
implicit "and none of the prior predicates matched":

- `VAL-001` — empty/null card number rejected first → `ERR` path returns `InvalidCC("Invalid: empty/null card number")`.
  Grounds on the helper `isNullOrEmpty()` (`cardNumber == null || cardNumber.trim().equals("") || length()==0`).
- `VAL-002` — card number over 19 digits rejected → `InvalidCC("Invalid: more than 19 digits")`. Constant: `length() > 19`.
- `VAL-003` — non-numeric card number rejected → `InvalidCC("Invalid: non numeric characters")`. Constant: regex `[0-9]+`.
- `RULE-004..007` — card-type classification, **in order**: Visa (len 13, or 16 with first digit 4) →
  Master (len 16, first 5, second 1-5) → AmEx (len 15, first 3, second 4/7) → Discover (len 16, starts `6011`).
  Each is a `RULE` whose predicate is the corresponding `isXxxCC` helper (read each body; they hold the parity constants).
- `ERR-008` — fallthrough: a number that passes the format guards but matches no scheme →
  `InvalidCC("Invalid: Not a possible card number")`.

`legacy_components` for all of these = this method's SymbolId **plus** the helper SymbolIds it
calls (`isNullOrEmpty`, `exceeds19Digit`, `hasAplhabets`, `isVisaCC`, `isMasterCC`, `isAmExCC`,
`isDiscoverCC`) — the `calls` edges are in the graph; widen the ring to pull the helper bodies
so the constants are grounded, not guessed.

Note the **overload trap**: there are two `createCreditCard` methods (one takes `String[]`, one
takes `CreditCardEntry`) — names are not unique. The annotation is SymbolId-keyed precisely for
this; resolve name→SymbolId (`wicked_estate resolve-symbol-id`) before writing so the rule binds
the exact overload. The `String[]` overload returns `null` on no-match (commented-out throw)
instead of an `InvalidCC` — that is a **different error semantics** and a separate rule; do not
merge the two overloads.

---

## Strategy / interface dispatch

`IReaderStrategy` (interface, 3 impls via `implements` edges: `CSVReader`, `JSONReader`,
`XMLReader`, each overriding `readFile(String)`). Treat the **interface method as the contract
rule** ("read input file → `List<CreditCardEntry>`") and **each impl as a variant rule** (CSV
splits on `,`; JSON/XML parse their format). The dispatch point — `CreditCardClient.getFileType()`
choosing the strategy by extension — is itself a `RULE` (file-type → reader selection).

`CSVReader.readFile` also shows the **swallowed-exception** idiom:
```java
try { ... if (line == null) throw new IllegalArgumentException("File is empty");
      if (entry.length > 4) throw new ArrayIndexOutOfBoundsException(); ... }
catch (Exception e) { e.printStackTrace(); }
return entries;   // returns partial/empty list on ANY failure
```
That `catch` swallowing every exception and returning a partial list is a real behavior:
`ERR-###` "on malformed/empty CSV the reader logs and returns whatever it parsed so far" —
flag RISK because the failure is silent. The two `throw`s inside (`"File is empty"`,
`ArrayIndexOutOfBoundsException` when a row has >4 fields) are their own `ERR-###` / `VAL-###`
items, even though the catch neutralizes them — capture intent and actual behavior both.

---

## Crawl checklist (hold these, find your own path)

1. `wicked_estate source <method>` — read the **body**, not the Javadoc.
2. Each `if`/`else if`/`switch` branch → its own rule; preserve ordering semantics.
3. Each `throw` and each swallowing `catch` → `ERR-###` (swallowing → also RISK).
4. Every numeric/length/range literal → pinned in a `parity_rule` (guardrail e).
5. `interface`+`implements` → contract rule + one rule per impl; the dispatch site is a rule.
6. Resolve name→SymbolId before writing (overloads/`@Override` collide on name).
7. `legacy_components` = the method SymbolId + every helper it `calls` (widen the ring for their bodies).
8. Annotation effects are CLAIMS until confirmed against the method/validator body.
