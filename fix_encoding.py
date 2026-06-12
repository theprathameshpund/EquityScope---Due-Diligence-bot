"""Fix Windows-1252 mojibake in report-view.ts.

The emojis in the file were double-encoded: UTF-8 bytes misread as
Windows-1252 then re-encoded as UTF-8.  We reverse that here.
"""
import sys

FILES = [
    r"frontend\src\app\components\report-view.ts",
]


def reverse_mojibake(content: str) -> str:
    result = []
    i = 0
    while i < len(content):
        c = content[i]
        if ord(c) > 127:
            # Collect contiguous non-ASCII run
            j = i
            while j < len(content) and ord(content[j]) > 127:
                j += 1
            chunk = content[i:j]
            try:
                fixed = chunk.encode("windows-1252").decode("utf-8")
                result.append(fixed)
            except (UnicodeEncodeError, UnicodeDecodeError):
                result.append(chunk)
            i = j
        else:
            result.append(c)
            i += 1
    return "".join(result)


for path in FILES:
    with open(path, "r", encoding="utf-8") as fh:
        original = fh.read()

    fixed = reverse_mojibake(original)

    changed = sum(1 for a, b in zip(original, fixed) if a != b)
    print(f"{path}: {changed} characters changed, "
          f"len {len(original)} -> {len(fixed)}")

    # Spot-check
    for fragment in ["sec-icon", "ticker-badge", "sentimentIcon"]:
        idx = fixed.find(fragment)
        if idx != -1:
            print(f"  {fragment}: ...{fixed[idx:idx+60]!r}...")

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(fixed)

print("Done.")
