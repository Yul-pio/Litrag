"""纯 Python 的 PDF 文本提取器（零第三方依赖）。

为什么需要它：
    正常情况我们会 `pip install pymupdf`，但如果机器上不了外网，
    装不了任何解析库。那就自己解 PDF。

它做什么：
    1. 扫描 PDF 里的所有对象（N 0 obj ... endobj）
    2. 用 zlib 解压 FlateDecode 压缩的流
    3. 找到每一页的 /Contents 内容流
    4. 解析内容流里的文本操作符（Tj / TJ / ' / "），并按 Td / TD / T* / Tm 断行
    5. 用字体的 /ToUnicode CMap 把字节映射成 Unicode；没有 CMap 就按 latin-1 兜底

它的边界（必须诚实说明）：
    - 扫描件（整页是图片）提取不出来，因为没有文字层
    - 极少数用对象流（/ObjStm）压缩的 PDF 可能解析失败
    - 复杂排版（双栏、表格）会丢掉结构，但文字顺序大体正确
    对「切块 + 检索」来说，这个质量是够用的。

这个文件本身也是一个很好的复试素材：
「我做过一个不依赖任何第三方库的 PDF 文本提取器，踩过 X 个坑」。
"""

from __future__ import annotations

import re
import zlib

_OBJ_HEAD = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
_STREAM_KW = b"stream"
_ENDSTREAM_KW = b"endstream"


# ---------------------------------------------------------------- 对象解析
def _inflate(raw: bytes) -> bytes | None:
    """解压 FlateDecode 流。PDF 里偶尔有非标准头部，多试几种方式。"""
    candidates = [raw]
    # 有些文件在流前面多了一个换行
    if raw[:1] in (b"\n", b"\r"):
        candidates.append(raw.lstrip(b"\r\n"))

    for chunk in candidates:
        for decompress in (
            zlib.decompress,
            lambda b: zlib.decompressobj().decompress(b),
            lambda b: zlib.decompressobj(-15).decompress(b),  # 无 zlib 头
        ):
            try:
                out = decompress(chunk)
                if out:
                    return out
            except Exception:
                continue
    return None


def parse_objects(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """返回 {对象号: (字典部分, 流数据)}。

    手工扫描而不是用正则全量匹配，因为二进制流里也可能出现 "N 0 obj" 字样，
    全量匹配会误判。
    """
    objects: dict[int, tuple[bytes, bytes | None]] = {}
    pos = 0
    while True:
        match = _OBJ_HEAD.search(data, pos)
        if not match:
            break

        num = int(match.group(1))
        body_start = match.end()
        endobj = data.find(b"endobj", body_start)
        stream_kw = data.find(_STREAM_KW, body_start)

        has_stream = stream_kw != -1 and (endobj == -1 or stream_kw < endobj)
        if not has_stream:
            objects[num] = (data[body_start:endobj if endobj != -1 else len(data)], None)
            pos = (endobj + 6) if endobj != -1 else len(data)
            continue

        meta = data[body_start:stream_kw]
        cursor = stream_kw + len(_STREAM_KW)
        if data[cursor : cursor + 2] == b"\r\n":
            cursor += 2
        elif data[cursor : cursor + 1] in (b"\n", b"\r"):
            cursor += 1

        # /Length 可能是直接数字，也可能是间接引用（12 0 R）
        stream = None
        length_match = re.search(rb"/Length\s+(\d+)(?!\s+\d+\s+R)", meta)
        if length_match:
            length = int(length_match.group(1))
            tail = data[cursor + length : cursor + length + 32]
            if length > 0 and _ENDSTREAM_KW in tail:
                stream = data[cursor : cursor + length]
                pos = cursor + length + len(_ENDSTREAM_KW)
        if stream is None:
            endstream = data.find(_ENDSTREAM_KW, cursor)
            if endstream == -1:
                endstream = len(data)
            stream = data[cursor:endstream].rstrip(b"\r\n")
            pos = endstream + len(_ENDSTREAM_KW)

        objects[num] = (meta, stream)

    return objects


def _stream_of(objects, num: int) -> bytes | None:
    item = objects.get(num)
    if not item:
        return None
    meta, stream = item
    if stream is None:
        return None
    if b"/FlateDecode" in meta:
        return _inflate(stream)
    return stream


# ---------------------------------------------------------------- 字体与 CMap
def _hex_to_text(hex_bytes: bytes) -> str:
    """ToUnicode 里的目标串是 UTF-16BE，每 4 个十六进制位一个字符。"""
    try:
        return bytes.fromhex(hex_bytes.decode("ascii")).decode("utf-16-be", errors="ignore")
    except Exception:
        return ""


def parse_tounicode(cmap: bytes) -> dict[bytes, str]:
    """解析 CMap 里的 bfchar / bfrange，得到「原始字节 -> 字符串」。"""
    mapping: dict[bytes, str] = {}

    for block in re.finditer(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block.group(1)):
            try:
                mapping[bytes.fromhex(src.decode())] = _hex_to_text(dst)
            except ValueError:
                continue

    for block in re.finditer(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        body = block.group(1)
        pattern = rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])"
        for lo_hex, hi_hex, dest in re.findall(pattern, body):
            try:
                lo = int(lo_hex, 16)
                hi = int(hi_hex, 16)
            except ValueError:
                continue
            if hi < lo or hi - lo > 65535:
                continue

            if dest.startswith(b"["):
                items = re.findall(rb"<([0-9A-Fa-f]+)>", dest)
                for offset, item in enumerate(items):
                    if lo + offset > hi:
                        break
                    width = len(lo_hex) // 2
                    key = (lo + offset).to_bytes(width, "big")
                    mapping[key] = _hex_to_text(item)
            else:
                base_hex = dest.strip(b"<>")
                if not base_hex:
                    continue
                base = int(base_hex, 16)
                width = len(lo_hex) // 2
                for code in range(lo, hi + 1):
                    key = code.to_bytes(width, "big")
                    mapping[key] = chr(base + (code - lo))

    return mapping


def _font_decoder(objects, font_num: int):
    """给一个字体对象，返回 (解码函数, 代码宽度)。"""
    item = objects.get(font_num)
    if not item:
        return None

    meta = item[0]
    to_unicode = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", meta)
    if to_unicode:
        cmap_data = _stream_of(objects, int(to_unicode.group(1)))
        if cmap_data:
            mapping = parse_tounicode(cmap_data)
            if mapping:
                width = max(len(k) for k in mapping)

                def decode(raw: bytes, _m=mapping, _w=width) -> str:
                    out = []
                    for i in range(0, len(raw) - _w + 1, _w):
                        out.append(_m.get(raw[i : i + _w], ""))
                    return "".join(out)

                return decode

    # 没有 ToUnicode（或解析失败）：按 latin-1 兜底，至少 ASCII 部分是对的
    def fallback(raw: bytes) -> str:
        return raw.decode("latin-1", errors="ignore")

    return fallback


def _balanced_dict(buf: bytes, start: int) -> bytes:
    """从 buf[start] 开始（必须是 '<<'），返回配对的整个字典内容。

    为什么不能直接用正则 `<<.*?>>`：PDF 的字典是嵌套的，
    比如 /Resources << /Font << /F1 1 0 R >> >>，
    非贪婪正则会停在第一个 >>，把 /Font 整个吞掉。
    """
    if buf[start : start + 2] != b"<<":
        return b""
    depth = 0
    i = start
    while i < len(buf) - 1:
        pair = buf[i : i + 2]
        if pair == b"<<":
            depth += 1
            i += 2
            continue
        if pair == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                return buf[start:i]
            continue
        i += 1
    return buf[start:]


def _find_dict_value(buf: bytes, key: bytes) -> bytes:
    """在字典文本里找 /Key 的值；如果值是 <<...>> 就返回完整字典，否则返回值片段。"""
    match = re.search(rb"/" + key + rb"\s*", buf)
    if not match:
        return b""
    start = match.end()
    if buf[start : start + 2] == b"<<":
        return _balanced_dict(buf, start)
    # 间接引用： 5 0 R
    ref = re.match(rb"(\d+)\s+\d+\s+R", buf[start:])
    return ref.group(0) if ref else b""


def _page_fonts(objects, page_meta: bytes) -> dict[str, object]:
    """从页面资源里取出 字体名 -> 解码函数。"""
    fonts: dict[str, object] = {}

    block = _find_dict_value(page_meta, b"Resources")
    if not block:
        return fonts
    if not block.startswith(b"<<"):
        ref = int(re.match(rb"(\d+)", block).group(1))
        block = objects.get(ref, (b"", None))[0]

    font_dict = _find_dict_value(block, b"Font")
    if not font_dict:
        return fonts
    if not font_dict.startswith(b"<<"):
        ref = int(re.match(rb"(\d+)", font_dict).group(1))
        font_dict = objects.get(ref, (b"", None))[0]

    for name, num in re.findall(rb"/([A-Za-z0-9#]+)\s+(\d+)\s+\d+\s+R", font_dict):
        decoder = _font_decoder(objects, int(num))
        if decoder:
            fonts[name.decode("latin-1")] = decoder
    return fonts


# ---------------------------------------------------------------- 内容流解析
_TOKEN = re.compile(
    rb"""
      \((?:\\.|[^\\()])*\)        # literal string
    | <[0-9A-Fa-f\s]*>            # hex string
    | \[[^\]]*\]                  # array (for TJ)
    | /[A-Za-z0-9#.+-]+           # name
    | [-+]?\d*\.?\d+              # number
    | [A-Za-z*'"]+                # operator
    """,
    re.X,
)

_STRING = re.compile(rb"\((?:\\.|[^\\()])*\)")


def _unescape_literal(raw: bytes) -> bytes:
    """处理 PDF 字面字符串里的转义：\\( \\) \\\\ \\n \\ddd 等。"""
    out = bytearray()
    i = 0
    body = raw[1:-1]
    while i < len(body):
        ch = body[i : i + 1]
        if ch == b"\\" and i + 1 < len(body):
            nxt = body[i + 1 : i + 2]
            mapping = {
                b"n": b"\n", b"r": b"\r", b"t": b"\t",
                b"b": b"\b", b"f": b"\f",
                b"(": b"(", b")": b")", b"\\": b"\\",
            }
            if nxt in mapping:
                out += mapping[nxt]
                i += 2
                continue
            if nxt.isdigit():
                octal = body[i + 1 : i + 4]
                out.append(int(octal, 8) & 0xFF)
                i += 1 + len(octal)
                continue
            out += nxt
            i += 2
            continue
        out += ch
        i += 1
    return bytes(out)


def _decode_string_token(token: bytes, decoder) -> str:
    if token.startswith(b"("):
        raw = _unescape_literal(token)
    else:
        hex_body = re.sub(rb"\s", b"", token.strip(b"<>"))
        if len(hex_body) % 2:
            hex_body += b"0"
        try:
            raw = bytes.fromhex(hex_body.decode("ascii"))
        except ValueError:
            return ""
    if decoder is None:
        return raw.decode("latin-1", errors="ignore")
    return decoder(raw)


_TJ_ITEM = re.compile(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>|[-+]?\d*\.?\d+")


def _as_numbers(tokens: list[bytes]) -> list[float]:
    numbers = []
    for token in tokens:
        try:
            numbers.append(float(token))
        except ValueError:
            continue
    return numbers


def _unwrap(text: str) -> str:
    """把 PDF 的「硬换行」还原成自然段落。

    PDF 里每一视觉行后面都有一个换行符，如果直接拿去做句子切分，
    得到的全是 "tualizes the broader scope of RAG research within the"
    这种半截句子。这里做一个启发式合并：

      - 上一行结尾没有句末标点，且下一行以小写字母开头 → 认为是被排版截断的，接上
      - 上一行以连字符结尾、下一行小写 → 断词，去掉连字符直接拼
      - 其余情况保留换行
    """
    ends_sentence = (".", "!", "?", ":", ";", "。", "！", "？", "：", "；", "）", ")")

    lines = [line.strip() for line in text.split("\n")]
    merged: list[str] = []

    for line in lines:
        if not line:
            if merged and merged[-1] != "":
                merged.append("")
            continue

        if merged and merged[-1]:
            prev = merged[-1]
            if prev.endswith("-") and line[:1].islower():
                merged[-1] = prev[:-1] + line
                continue
            if prev.endswith("-") and line[:1].isdigit():
                merged[-1] = prev[:-1] + line
                continue
            if not prev.endswith(ends_sentence) and (line[:1].islower() or prev.endswith(",")):
                merged[-1] = prev + " " + line
                continue

        merged.append(line)

    return "\n".join(merged)


def extract_stream_text(content: bytes, fonts: dict[str, object]) -> str:
    """把一个内容流解析成文本，尽量保留词间空格和行结构。

    关键细节（也是我调试了半天的地方）：
      PDF 里单词之间的空格并不是空格字符，而是 TJ 数组里的字距指令，
      例如 [(Retrie)-350(val)]TJ，-350 表示"往右退 0.35 个字符宽"。
      必须边读字符串边处理这些数字，才能得到 "Retrieval" 而不是
      "Retrieval" 后面跟着一堆空格。
    """
    out: list[str] = []
    current = None
    size = 10.0  # 当前字号：用来区分"换行"和"上标"
    pending: list[bytes] = []

    for match in _TOKEN.finditer(content):
        token = match.group(0)
        head = token[:1]

        if head in (b"(", b"<", b"[") or head == b"/":
            pending.append(token)
            continue

        if head.isdigit() or head in (b"-", b"+", b"."):
            pending.append(token)
            continue

        op = token  # 到这里就是操作符

        if op == b"Tf":
            # 栈： /F1 12 Tf
            names = [p for p in pending if p.startswith(b"/")]
            numbers = _as_numbers(pending)
            if names:
                name = names[-1][1:].decode("latin-1")
                current = fonts.get(name)
            if numbers:
                size = abs(numbers[0]) or 10.0
            pending.clear()
            continue

        if op in (b"Tj", b"'", b'"'):
            strings = [p for p in pending if p.startswith(b"(") or p.startswith(b"<")]
            if strings:
                text = _decode_string_token(strings[-1], current)
                if op in (b"'", b'"'):
                    out.append("\n")
                out.append(text)
            pending.clear()
            continue

        if op == b"TJ":
            arrays = [p for p in pending if p.startswith(b"[")]
            if arrays:
                inner = arrays[-1][1:-1]
                # 必须按出现顺序逐项处理：字符串直接输出，数字判断是不是空格
                for item in _TJ_ITEM.finditer(inner):
                    piece = item.group(0)
                    if piece[:1] in (b"(", b"<"):
                        out.append(_decode_string_token(piece, current))
                    else:
                        try:
                            if float(piece) < -120:  # 约 0.12 个字宽，判为词间空格
                                out.append(" ")
                        except ValueError:
                            continue
            pending.clear()
            continue

        if op in (b"T*", b"TD"):
            out.append("\n")
            pending.clear()
            continue

        if op == b"Td":
            numbers = _as_numbers(pending)
            if len(numbers) >= 2:
                dx, dy = numbers[-2], numbers[-1]
                # 纵向位移超过半个字高才算换行；否则是上标/下标，留在本行
                if abs(dy) > 0.5 * size:
                    out.append("\n")
                elif abs(dx) > 0.5 * size:
                    out.append(" ")
            pending.clear()
            continue

        if op == b"Tm":
            out.append("\n")
            pending.clear()
            continue

        if op in (b"BT", b"ET"):
            out.append("\n")
            pending.clear()
            continue

        pending.clear()

    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- 对外接口
def extract_pdf_text(path) -> list[tuple[int, str]]:
    """返回 [(页码, 文本), ...]。"""
    data = open(path, "rb").read()
    objects = parse_objects(data)

    page_nums = [
        num
        for num, (meta, _) in objects.items()
        if re.search(rb"/Type\s*/Page\b", meta) and b"/Pages" not in meta[:400]
    ]
    page_nums.sort()

    pages: list[tuple[int, str]] = []
    for index, num in enumerate(page_nums, start=1):
        meta = objects[num][0]
        fonts = _page_fonts(objects, meta)

        contents: list[int] = []
        array_match = re.search(rb"/Contents\s*\[(.*?)\]", meta, re.S)
        if array_match:
            contents = [int(n) for n in re.findall(rb"(\d+)\s+\d+\s+R", array_match.group(1))]
        else:
            single = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", meta)
            if single:
                contents = [int(single.group(1))]

        chunks = []
        for obj_num in contents:
            stream = _stream_of(objects, obj_num)
            if stream:
                chunks.append(extract_stream_text(stream, fonts))

        text = "\n".join(c for c in chunks if c).strip()
        if text:
            pages.append((index, _unwrap(text)))

    return pages
