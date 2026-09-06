# Script laws and executable explanations

The Aver pin in `.aver-version` includes `because`, `using` and checked list
induction. The Script laws can be checked together, including their imported
helpers, from the interpreter entry:

```sh
aver proof domain/interp.av --module-root . --check-json -o /tmp/script-laws
```

At pin `9b064b4fca9d02c87bc1a915309a97290530a1c8`, Lean reports **70 universal
laws, 2 bounded laws and no open laws**. The strict command still exits 1
because 130 non-law claims in the wider graph are deliberately declined. The generated `proof_manifest.json`
records the tier and kernel dependencies of each law.

Of the original twenty laws in PR #338, eighteen now close universally, up
from fourteen. The extra fifty-two laws are counted separately: eight
roundtrip and induction additions, seventeen canonical-encoding laws, twelve
helpers that establish the exact arithmetic-width boundary, and fifteen
helpers that establish the CompactSize roundtrip. The existing function bodies and public API are unchanged apart
from the original PR's `littleEndian` guard repair.

## Number guarantees

These laws quantify over all Aver integers, including values larger than the
four-byte arithmetic operand range:

- `asNumber.readsWhatFromNumberWrote`: reading an encoded number gives the
  original number, for either sign and across sign-byte boundaries.
- `isMinimalNumber.acceptsWhatFromNumberWrites`: the encoding has no
  redundant top byte.
- `fromNumber.encodingIdentifiesTheNumber`: two encodings are equal exactly
  when their numbers are equal. Different numbers cannot collide.
- `asNumber.rewritingPreservesTheNumber`: reading an item, writing its number
  minimally and reading it again preserves the value. This includes redundant
  zero bytes and negative zero; it does not claim the original bytes survive.

The first two use executable explanations which split zero from nonzero,
name the most significant digit, and connect its properties to sign placement.
The next two cite the proved roundtrip with `using`; no new justification
function is needed for either consequence.

## Canonical Script numbers

For every list whose elements are octets (`0 <= byte < 256`),
`fromNumber.canonicalExactlyWhenMinimal` proves:

```text
fromNumber(asNumber(bytes)) == bytes  iff  isMinimalNumber(bytes)
```

Thus the minimality checker recognizes exactly the fixed points of number
normalization. Negative zero `[128]` normalizes to `[]`; redundant `[1, 0]`
normalizes to `[1]`; the necessary sign byte in `[128, 0]` survives.
The octet premise matters: the out-of-domain list `[256]` passes the minimality
predicate but normalizes to `[128, 128]`, so the theorem deliberately excludes it.

The hard direction is `fromNumber.minimalItemsAreFixedPoints`. Its two
explanations first recover the magnitude digits, then restore the top byte or
separate sign byte. `bigEndian.writingReadDigitsPreservesThem` supplies checked
list induction: the recursive explanation consumes one byte and updates the
positive prefix. Each explanation and the final implication are independently
universal and kernel-audited. The Aver pin includes generic compiler fixes found
while checking these proofs; no Bitcoin-specific compiler logic or handwritten
Lean is used.

The reverse direction cites the already-proved minimality of every encoder
output. `asNumber.minimalEncodingIdentifiesBytes` then proves that two minimal byte
encodings are equal exactly when they decode to the same number.

## An induction written in Aver

`bigEndian.largerPrefixStaysLarger` says that reading the same list preserves
the strict order of two accumulators. It holds for every integer list, so it
also holds for byte lists of any length:

```aver
fn largerPrefixReason(bytes: List<Int>, lower: Int, upper: Int) -> Bool
    match bytes
        [] -> lower < upper
        [head, ..tail] -> Bool.and(
            largerPrefixReason(tail, lower * 256 + head, upper * 256 + head),
            bigEndian(bytes, lower) < bigEndian(bytes, upper)
        )
```

The law assumes `lower < upper`, cites this function with `because`, and has
`using []`. Both accumulators advance together; the checked decreasing
argument is the list tail. Lean checks the recursive step under the original
assumption, checks that assumption at the recursive call, and proves the
original law from the explanation. The function is also run by ordinary
`verify` and `verify --hostile`.

Measured with the same compiler and source, this law is bounded without proof
annotations, open with `using []` alone, and universal with the recursive
explanation. Both explanation and implication receive universal credit.
The source theorem uses only `Classical.choice`, `Quot.sound` and `propext`;
it has no `sorryAx` or dependency on sample evaluation.

## Exact arithmetic-width boundary

`fitsArithmetic.acceptsCoreOperandRange` is now universal:

```text
fitsArithmetic(fromNumber(value))  iff  -2147483648 < value < 2147483648
```

This includes both signs and excludes both endpoints. A magnitude of 2147483648
needs another byte for its sign, so even -2147483648 is outside the four-byte
operand range. The proof composes the exact one-, two-, three-, and four-byte
thresholds: 128, 32768, 8388608 and 2147483648.

The common step is `signedBytes.lengthStep`: above a single unsigned byte,
the low byte adds one to the length of the quotient's signed encoding.
`sizeRecurrenceReason` is the same executable explanation at each threshold.
The sign-placement law preserves a prepended byte whenever the tail is nonempty;
zero and magnitudes below 256 provide the base cases. Existing production
function bodies and the public API stay unchanged.

This exposed a generic Aver bug: `using` lemmas disappeared from the final
implication when `because` was present. Aver PR #1296 removes that exception.
No width-specific compiler rule or handwritten Lean is involved.

## CompactSize preserves the following field

`CompactSize.encode.readsBack` is universal for every unsigned 64-bit value
and every trailing list:

```text
read(encode(value) ++ rest)
  == Count(value, rest, length(encode(value)))
```

The decoder recovers the value, consumes exactly the encoded field, and leaves
the complete suffix untouched. This covers every marker boundary (253, 65536,
4294967296), including the maximum value 18446744073709551615. It asserts a
roundtrip for encoder output; it does not claim that the decoder rejects
noncanonical external encodings.

Fifteen helper laws establish fixed-width little-endian readback, field length,
and suffix preservation. `readBackReason` is an ordinary private Bool function
splitting the four wire widths: 1, 3, 5 and 9 bytes. The helper width guards cover
up to eight payload bytes, so hostile checks stay executable even when they
try extreme integers. The original roundtrip domain is unchanged.

Aver PR #1298 derives a native countdown measure from existing guard and shrink
checks and makes its equations available to `using`. This removes law-family
special cases in the compiler. It also fixes imported record identities inside
explanations. The complete proof is Aver source; no handwritten Lean is needed.

## Remaining limits

There are no open laws in this export. The command still reports 130 declined
non-law claims in the wider interpreter dependency cone; they are neither exported
nor proved and are separate from the law counts.

Two laws retain bounded credit: `ScriptState.rearranged.staysWithinDeclaredDepth`
and `StackItem.isMinimalPush.directPushIsMinimalUnlessSmallNumber`. All previously
universal laws retain their credit.
