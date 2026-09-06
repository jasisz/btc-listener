# Script laws and executable explanations

The Aver pin in `.aver-version` includes `because`, `using` and checked list
induction. The Script laws can be checked together, including their imported
helpers, from the interpreter entry:

```sh
aver proof domain/interp.av --module-root . --check-json -o /tmp/script-laws
```

At pin `5b892fd1e16ca6f855913d6e84fb4b12094e79d5`, Lean reports **41 universal
laws, 3 bounded laws and 1 open law**. The strict command exits 1 because
the open law remains an obligation. The generated `proof_manifest.json`
records the tier and kernel dependencies of each law.

Of the original twenty laws in PR #338, sixteen now close universally, up
from fourteen. The extra twenty-five laws are counted separately: the original eight
roundtrip and induction additions, followed by seventeen laws establishing
canonical encoding and its supporting digit, byte-validity and sign facts. The existing function bodies and public API are unchanged apart
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
universal and kernel-audited. The Aver pin includes generic exporter fixes found
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

## What is still open

`fitsArithmetic.acceptsCoreOperandRange` is still not proved universally:
the encoding should fit in four bytes exactly for `-2^31 < value < 2^31`.
Its examples pass, but neither that result nor the decoder induction above
is presented as a proof of this boundary. The command also reports 130 declined
non-law claims in the wider interpreter dependency cone; those were not
exported or proved and are separate from the law counts above.

Three laws retain bounded credit: `CompactSize.encode.readsBack`,
`ScriptState.rearranged.staysWithinDeclaredDepth`, and
`StackItem.isMinimalPush.directPushIsMinimalUnlessSmallNumber`. All fourteen
previously universal laws retain their credit.
