"""qr: a tiny QR Code encoder (byte mode, error correction L, versions 1 to 10).

Stdlib only, so `tap.py pair` and the /pair page can show a pairing code
without a dependency. encode(text) -> rows of bools (True = dark);
terminal(text) and svg(text) render it.
"""
import re

# version -> (error-correction codewords per block, data codewords of each block), level L
_BLOCKS = {1: (7, [19]), 2: (10, [34]), 3: (15, [55]), 4: (20, [80]), 5: (26, [108]),
           6: (18, [68] * 2), 7: (20, [78] * 2), 8: (24, [97] * 2), 9: (30, [116] * 2),
           10: (18, [68, 68, 69, 69])}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_MASKS = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
          lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
          lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
          lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]

_EXP, _LOG = [0] * 512, [0] * 256
_v = 1
for _i in range(255):
    _EXP[_i], _LOG[_v] = _v, _i
    _v = (_v << 1) ^ (0x11D if _v & 0x80 else 0)
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return _EXP[_LOG[a] + _LOG[b]] if a and b else 0


def rs(data, n):
    """The n Reed-Solomon error-correction codewords for data (GF(256), poly 0x11D)."""
    gen = [1]
    for i in range(n):
        gen = [a ^ _mul(b, _EXP[i]) for a, b in zip(gen + [0], [0] + gen)]
    rem = [0] * n
    for d in data:
        f = d ^ rem[0]
        rem = [r ^ _mul(g, f) for r, g in zip(rem[1:] + [0], gen[1:])]
    return rem


def _codewords(raw):
    for ver in range(1, 11):
        ec, blocks = _BLOCKS[ver]
        cap = sum(blocks)
        cc = 8 if ver < 10 else 16
        if 4 + cc + 8 * len(raw) <= cap * 8:
            break
    else:
        raise ValueError(f"{len(raw)} bytes is too long for a version 10-L code")
    bits = "0100" + format(len(raw), f"0{cc}b") + "".join(format(b, "08b") for b in raw)
    bits += "0" * min(4, cap * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    data = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    data += [0xEC, 0x11] * ((cap - len(data)) // 2) + [0xEC] * ((cap - len(data)) % 2)
    split, at = [], 0
    for n in blocks:
        split.append(data[at:at + n])
        at += n
    out = [b[i] for i in range(max(blocks)) for b in split if i < len(b)]
    eccs = [rs(b, ec) for b in split]
    return ver, out + [e[i] for i in range(ec) for e in eccs]


def encode(text, mask=None):
    raw = text.encode() if isinstance(text, str) else bytes(text)
    ver, words = _codewords(raw)
    size = 17 + 4 * ver
    m = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def put(x, y, dark):
        m[y][x], fn[y][x] = dark, True

    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < size and 0 <= cy + dy < size:
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    al = _ALIGN[ver]
    for i, ax in enumerate(al):
        for j, ay in enumerate(al):
            if (i, j) not in ((0, 0), (0, len(al) - 1), (len(al) - 1, 0)):
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vb = ver << 12 | rem
        for i in range(18):
            a, b, bit = size - 11 + i % 3, i // 3, bool(vb >> i & 1)
            put(a, b, bit)
            put(b, a, bit)

    def fmt(grid, k):
        data = 1 << 3 | k                       # level L = 01
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        b = (data << 10 | rem) ^ 0x5412
        bit = [bool(b >> i & 1) for i in range(15)]
        spots = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + \
                [(14 - i, 8) for i in range(9, 15)]
        spots2 = [(size - 1 - i, 8) for i in range(8)] + [(8, size - 15 + i) for i in range(8, 15)]
        for (x, y), v in list(zip(spots, bit)) + list(zip(spots2, bit)):
            grid[y][x] = v
            fn[y][x] = True
        grid[size - 8][8] = fn[size - 8][8] = True   # the dark module

    fmt(m, 0)                                       # reserve the format areas
    i, nbits, right = 0, len(words) * 8, size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if (right + 1) & 2 == 0 else vert
                if not fn[y][x] and i < nbits:
                    m[y][x] = bool(words[i >> 3] >> (7 - (i & 7)) & 1)
                    i += 1
        right -= 2

    def masked(k):
        g = [[m[y][x] ^ (not fn[y][x] and _MASKS[k](x, y)) for x in range(size)]
             for y in range(size)]
        fmt(g, k)
        return g

    if mask is not None:
        return masked(mask)
    return min((masked(k) for k in range(8)), key=_penalty)


def _penalty(g):
    n = len(g)
    lines = ["".join("1" if c else "0" for c in r) for r in g]
    lines += ["".join(lines[y][x] for y in range(n)) for x in range(n)]
    p = sum(len(r.group()) - 2 for s in lines for r in re.finditer(r"0{5,}|1{5,}", s))
    p += 40 * sum(len(re.findall(f"(?={pat})", s)) for s in lines
                  for pat in ("10111010000", "00001011101"))
    p += 3 * sum(1 for y in range(n - 1) for x in range(n - 1)
                 if g[y][x] == g[y][x + 1] == g[y + 1][x] == g[y + 1][x + 1])
    dark = sum(map(sum, g))
    return p + 10 * (abs(dark * 20 - n * n * 10) // (n * n))


def terminal(text, quiet=2):
    """Two module rows per line with half blocks, in explicit black on bright white,
    so it scans from any terminal theme."""
    g = encode(text)
    n = len(g) + 2 * quiet

    def dark(x, y):
        x, y = x - quiet, y - quiet
        return 0 <= x < len(g) and 0 <= y < len(g) and g[y][x]
    return "\n".join("".join(f"\033[{30 if dark(x, y) else 97};{40 if dark(x, y + 1) else 107}m▀"
                             for x in range(n)) + "\033[0m" for y in range(0, n, 2))


def svg(text, quiet=4):
    g = encode(text)
    n = len(g) + 2 * quiet
    d = "".join(f"M{x + quiet},{y + quiet}h1v1h-1z"
                for y, row in enumerate(g) for x, c in enumerate(row) if c)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
            f'shape-rendering="crispEdges"><rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path d="{d}"/></svg>')


if __name__ == "__main__":
    import sys
    print(terminal(" ".join(sys.argv[1:]) or "https://example.com"))
