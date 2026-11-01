"""Deterministic normalization of raw extracted expressions (extraction protocol v2).

Agents submit the literal source text. Everything that turns text into a canonical
value — numbers, Chinese numerals and magnitudes, approximation and bounds, ranges,
tolerances, percentages, units, currencies, enums and dates — happens here, in
KnowledgeOS, so that every Agent platform produces identical stored values.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation


class NormalizationError(Exception):
    """A raw expression cannot be normalized; ``code`` is a protocol Issue code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def nfkc(value):
    return unicodedata.normalize("NFKC", str(value or ""))


def plain(number: Decimal) -> str:
    """Render a Decimal without exponent or redundant trailing zeros."""
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    text = format(number.normalize(), "f")
    return text


# ---------------------------------------------------------------------------
# Numbers

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_SECTIONS = {"万": 10 ** 4, "亿": 10 ** 8}
CN_CHARS = set(CN_DIGITS) | set(CN_UNITS) | set(CN_SECTIONS) | {"点"}


def parse_cn_integer(text: str) -> int:
    """Parse a Chinese integer numeral such as 三十, 两千零五, 一亿二千万."""
    if not text:
        raise ValueError("empty numeral")
    if all(char in CN_DIGITS for char in text) and len(text) > 1:
        return int("".join(str(CN_DIGITS[char]) for char in text))
    total, section, number = 0, 0, None
    for char in text:
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char in CN_UNITS:
            section += (1 if number is None else number) * CN_UNITS[char]
            number = None
        elif char in CN_SECTIONS:
            section += number or 0
            total = (total + section) * CN_SECTIONS[char] if char == "亿" else total + section * CN_SECTIONS[char]
            section, number = 0, None
        else:
            raise ValueError(f"not a Chinese numeral: {text}")
    return total + section + (number or 0)


def parse_cn_number(text: str) -> Decimal:
    whole, _, fraction = text.partition("点")
    value = Decimal(parse_cn_integer(whole)) if whole else Decimal(0)
    if fraction:
        if any(char not in CN_DIGITS for char in fraction):
            raise ValueError(f"invalid Chinese decimal: {text}")
        value += Decimal("0." + "".join(str(CN_DIGITS[char]) for char in fraction))
    return value


_ARABIC = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)")
_CHINESE = re.compile(r"[零〇一二两三四五六七八九十百千](?:[零〇一二两三四五六七八九十百千万亿]*)(?:点[零〇一二三四五六七八九]+)?")
MAGNITUDES = (("万亿", Decimal(10) ** 12), ("亿", Decimal(10) ** 8), ("万", Decimal(10) ** 4), ("千", Decimal(10) ** 3))


def _number_at(text, unit_words=()):
    """Return (Decimal, consumed_length) for a number at the start of ``text``."""
    match = _ARABIC.match(text)
    if match:
        value, end = Decimal(match.group(0)), match.end()
        rest = text[end:]
        for word, factor in MAGNITUDES:
            if rest.startswith(word) and not any(rest.startswith(unit) and len(unit) > len(word) for unit in unit_words if unit.startswith(word)):
                return value * factor, end + len(word)
        return value, end
    match = _CHINESE.match(text)
    if match:
        token = match.group(0)
        # A trailing 千/万 that starts a unit word (千瓦, 千克) belongs to the unit.
        while token and token[-1] in "千万" and any(text[len(token) - 1:].startswith(unit) for unit in unit_words if unit[0] == token[-1]):
            token = token[:-1]
        if token:
            try:
                return parse_cn_number(token), len(token)
            except ValueError:
                pass
    return None, 0


PREFIX_QUALIFIERS = [
    (("大约", "大概", "约为", "约", "近似", "接近", "近", "≈", "~"), ("approximation", "approximate")),
    (("不低于", "不少于", "不小于", "至少", "最少", "最低", "≥", ">="), ("bound", "lower")),
    (("不高于", "不超过", "不大于", "至多", "最多", "最高", "≤", "<="), ("bound", "upper")),
    (("超过", "高于", "大于", "多于", ">"), ("bound", "lower_exclusive")),
    (("低于", "少于", "小于", "<"), ("bound", "upper_exclusive")),
]
SUFFIX_QUALIFIERS = [
    (("左右", "上下", "前后"), ("approximation", "approximate")),
    (("及以上", "以上"), ("bound", "lower")),
    (("及以下", "以下", "以内", "之内"), ("bound", "upper")),
]
RANGE_SEPARATORS = ("~", "～", "—", "–", "-", "至", "到")


@dataclass
class ParsedNumber:
    value: Decimal
    unit_text: str = ""
    qualifiers: dict = field(default_factory=dict)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    tolerance: Decimal | None = None
    percent: bool = False


def parse_number_expression(raw, unit_words=()):
    """Parse 约30天, 不低于5kW, 10-20天, 1.2亿元, 10±0.5mm, 30%, 百分之三十 and plain numbers."""
    text = nfkc(raw).strip()
    text = re.sub(r"(?<=\d)[,，](?=\d{3}(?!\d))", "", text)
    text = re.sub(r"\s+", " ", text)
    qualifiers = {}
    for words, (key, value) in PREFIX_QUALIFIERS:
        word = next((item for item in words if text.startswith(item)), None)
        if word:
            qualifiers[key] = value
            text = text[len(word):].lstrip()
            break
    for words, (key, value) in SUFFIX_QUALIFIERS:
        word = next((item for item in words if text.endswith(item)), None)
        if word:
            qualifiers.setdefault(key, value)
            text = text[:-len(word)].rstrip()
            break
    percent = False
    if text.startswith("百分之"):
        percent, text = True, text[3:]
    value, used = _number_at(text, unit_words)
    if value is None:
        raise NormalizationError("VALUE_UNPARSEABLE", f"无法从“{raw}”中解析数值")
    rest = text[used:]
    result = ParsedNumber(value=value, qualifiers=qualifiers, percent=percent)
    first_unit = re.match(r"\s*([^\d±~～—–\-至到]*)", rest).group(1)
    after = rest[len(re.match(r"\s*[^\d±~～—–\-至到]*", rest).group(0)):].lstrip()
    if after.startswith("±"):
        tolerance, used = _number_at(after[1:].lstrip(), unit_words)
        if tolerance is None:
            raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”的公差无法解析")
        result.tolerance = tolerance
        result.unit_text = (first_unit.strip() or after[1:].lstrip()[used:].strip())
        return _finish(result, raw)
    separator = next((item for item in RANGE_SEPARATORS if after.startswith(item)), None)
    if separator:
        upper, used = _number_at(after[len(separator):].lstrip(), unit_words)
        if upper is None:
            raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”的区间上限无法解析")
        second_unit = after[len(separator):].lstrip()[used:].strip()
        if first_unit.strip() and second_unit and first_unit.strip() != second_unit:
            raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”的区间两端单位不一致")
        if upper < value:
            raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”的区间下限大于上限")
        result.minimum, result.maximum = value, upper
        result.unit_text = second_unit or first_unit.strip()
        return _finish(result, raw)
    if after:
        raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”包含多个数值或无法识别的内容")
    result.unit_text = first_unit.strip()
    return _finish(result, raw)


def _finish(result, raw):
    if result.unit_text in ("%", "％"):
        result.percent, result.unit_text = True, ""
    elif result.unit_text.endswith(("%", "％")):
        raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”的百分号位置无法识别")
    if result.percent:
        hundred = Decimal(100)
        result.value /= hundred
        result.minimum = result.minimum / hundred if result.minimum is not None else None
        result.maximum = result.maximum / hundred if result.maximum is not None else None
        result.tolerance = result.tolerance / hundred if result.tolerance is not None else None
    return result


# ---------------------------------------------------------------------------
# Dates

CN_QUARTERS = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


@dataclass
class ParsedTime:
    start: datetime | date
    precision: str  # day, month, quarter, year, minute, second
    explicit_offset: bool = False

    def end(self):
        """Exclusive end of the period."""
        value = self.start if isinstance(self.start, date) and not isinstance(self.start, datetime) else self.start.date()
        if self.precision == "year":
            return date(value.year + 1, 1, 1)
        if self.precision in ("month", "quarter"):
            months = 3 if self.precision == "quarter" else 1
            month = value.month - 1 + months
            return date(value.year + month // 12, month % 12 + 1, 1)
        if self.precision == "day":
            return value + timedelta(days=1)
        return self.start


def _offset(value):
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value or "")
    if not match:
        raise ValueError("default_utc_offset must look like +08:00")
    return value


def _to_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def reference_date(document_dates, captured_at):
    for key in ("effective_from", "issued_at"):
        value = (document_dates or {}).get(key)
        if value:
            parsed = parse_time(value, None)
            if parsed:
                start = parsed.start
                return start.date() if isinstance(start, datetime) else start
    if captured_at:
        return datetime.fromisoformat(captured_at.replace("Z", "+00:00")).date()
    return None


def parse_time(raw, reference: date | None):
    """Parse an absolute or relative time expression into a ParsedTime, or None."""
    text = nfkc(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return ParsedTime(parsed.date(), "day")
        return ParsedTime(parsed, "second" if parsed.second or ":" in text[13:] and text.count(":") >= 2 else "minute",
                          explicit_offset=parsed.tzinfo is not None)
    except ValueError:
        pass
    text = text.replace("号", "日").replace(" ", "")
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:(\d{1,2})[:时点](\d{2})分?)?", text)
    if match:
        year, month, day, hour, minute = match.groups()
        try:
            if hour is not None:
                return ParsedTime(datetime(int(year), int(month), int(day), int(hour), int(minute)), "minute")
            return ParsedTime(date(int(year), int(month), int(day)), "day")
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})月?(?:份)?", text)
    if match:
        year, month = map(int, match.groups())
        return ParsedTime(date(year, month, 1), "month") if 1 <= month <= 12 else None
    match = re.fullmatch(r"(\d{4})年?(?:第)?(?:Q|q)?([一二三四1-4])(?:季度|季)?", text)
    if match and ("季" in text or "Q" in text.upper()):
        year, quarter = int(match.group(1)), CN_QUARTERS[match.group(2)]
        return ParsedTime(date(year, quarter * 3 - 2, 1), "quarter")
    match = re.fullmatch(r"(\d{4})年(?:度)?", text)
    if match:
        return ParsedTime(date(int(match.group(1)), 1, 1), "year")
    if reference is None:
        return None
    match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", text)
    if match:
        try:
            return ParsedTime(date(reference.year, int(match.group(1)), int(match.group(2))), "day")
        except ValueError:
            return None
    relative_days = {"今天": 0, "今日": 0, "当天": 0, "当日": 0, "本日": 0, "昨天": -1, "昨日": -1,
                     "前天": -2, "明天": 1, "明日": 1, "次日": 1, "后天": 2}
    if text in relative_days:
        return ParsedTime(reference + timedelta(days=relative_days[text]), "day")
    week_start = reference - timedelta(days=reference.weekday())
    relative_weeks = {"本周": 0, "这周": 0, "上周": -1, "下周": 1}
    if text in relative_weeks:
        return ParsedTime(week_start + timedelta(weeks=relative_weeks[text]), "day")

    def shift_month(months):
        index = reference.year * 12 + reference.month - 1 + months
        return date(index // 12, index % 12 + 1, 1)

    relative_months = {"本月": 0, "当月": 0, "这个月": 0, "上月": -1, "上个月": -1, "下月": 1, "下个月": 1}
    if text in relative_months:
        return ParsedTime(shift_month(relative_months[text]), "month")
    relative_years = {"今年": 0, "本年": 0, "本年度": 0, "当年": 0, "去年": -1, "上年": -1, "上一年": -1, "明年": 1, "次年": 1}
    if text in relative_years:
        return ParsedTime(date(reference.year + relative_years[text], 1, 1), "year")
    match = re.fullmatch(r"(\d+|[一二两三四五六七八九十]+)(天|日|周|个月|月|年)(前|后|以后|之后|以前|之前)", text)
    if match:
        amount = int(match.group(1)) if match.group(1).isdigit() else parse_cn_integer(match.group(1))
        sign = -1 if match.group(3) in ("前", "以前", "之前") else 1
        unit = match.group(2)
        if unit in ("天", "日"):
            return ParsedTime(reference + timedelta(days=sign * amount), "day")
        if unit == "周":
            return ParsedTime(reference + timedelta(weeks=sign * amount), "day")
        if unit in ("个月", "月"):
            index = reference.year * 12 + reference.month - 1 + sign * amount
            year, month = index // 12, index % 12 + 1
            return ParsedTime(date(year, month, min(reference.day, calendar.monthrange(year, month)[1])), "day")
        year = reference.year + sign * amount
        return ParsedTime(date(year, reference.month, min(reference.day, calendar.monthrange(year, reference.month)[1])), "day")
    return None


def timestamp(value, offset):
    """Render a date or datetime as an ISO timestamp with an explicit UTC offset."""
    _offset(offset)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.isoformat()
        return value.replace(microsecond=0).isoformat() + offset
    return value.isoformat() + "T00:00:00" + offset


# ---------------------------------------------------------------------------
# Typed values

BOOLEANS = {"是": True, "有": True, "true": True, "yes": True, "y": True, "对": True, "符合": True,
            "否": False, "无": False, "没有": False, "false": False, "no": False, "n": False, "不符合": False}


def _lookup(mapping, text):
    """Exact, then NFKC/casefold lookup in a {raw: value} mapping."""
    if text in mapping:
        return mapping[text]
    folded = {nfkc(key).casefold().strip(): value for key, value in mapping.items()}
    return folded.get(nfkc(text).casefold().strip())


def _strip_currency(text, aliases):
    text = nfkc(text).strip()
    found = set()
    ordered = sorted(aliases, key=len, reverse=True)
    for _ in range(2):
        for alias in ordered:
            key = nfkc(alias)
            if text.startswith(key) and not (key.isalpha() and key.isascii() and text[len(key):len(key) + 1].isalpha()):
                found.add(aliases[alias])
                text = text[len(key):].strip()
                break
        for alias in ordered:
            key = nfkc(alias)
            if key and text.endswith(key) and not (key.isalpha() and key.isascii() and text[-len(key) - 1:-len(key)].isalpha()):
                found.add(aliases[alias])
                text = text[:-len(key)].strip()
                break
    return text, found


class ValueNormalizer:
    """Normalize RawValue objects for one registry and extraction profile."""

    def __init__(self, registry, profile):
        self.registry = registry
        self.profile = profile
        self.offset = _offset(profile.get("default_utc_offset", "+00:00"))

    def unit_aliases(self, predicate_id, card):
        aliases = dict(self.profile.get("unit_aliases") or {})
        aliases.update(((card or {}).get("properties", {}).get(predicate_id) or {}).get("unit_aliases") or {})
        return aliases

    def _resolve_unit(self, predicate, text, aliases):
        definition = predicate.get("value", {})
        allowed = list(definition.get("units") or [])
        default = definition.get("default_unit")
        text = nfkc(text).strip()
        if not text:
            if default:
                return default, None
            raise NormalizationError("UNIT_NOT_ALLOWED", f"{predicate['id']} 需要单位，原文未给出")
        unit = _lookup(aliases, text)
        if unit is None and (text in self.registry.units or text in allowed or text == default):
            unit = text
        if unit is None:
            if not allowed and not default and not definition.get("dimension"):
                return text, None
            raise NormalizationError("UNIT_NOT_ALLOWED", f"无法识别 {predicate['id']} 的单位写法“{text}”")
        if not allowed or unit in allowed:
            if definition.get("dimension") and (unit not in self.registry.units or self.registry.units[unit]["dimension"] != definition["dimension"]):
                raise NormalizationError("UNIT_NOT_ALLOWED", f"单位 {unit} 不属于 {predicate['id']} 的量纲")
            return unit, None
        units = self.registry.units
        target = default if default in allowed else allowed[0]
        if unit in units and target in units and units[unit]["dimension"] == units[target]["dimension"]:
            factor = Decimal(str(units[unit]["factor_to_base"])) / Decimal(str(units[target]["factor_to_base"]))
            return target, (unit, factor)
        raise NormalizationError("UNIT_NOT_ALLOWED", f"单位 {unit} 不在 {predicate['id']} 允许的单位 {', '.join(allowed)} 中")

    def normalize(self, predicate_id, raw_value, *, card=None, storage="assertion", ref_id=None):
        """Return (normalized_value, issues) where issues is a list of (code, severity, message)."""
        predicate = self.registry.predicates[predicate_id]
        definition = predicate.get("value", {})
        typ = definition.get("type")
        raw = str(raw_value.get("raw", ""))
        issues = []
        if typ in ("string", "text"):
            text = raw.strip()
            if not text:
                raise NormalizationError("VALUE_UNPARSEABLE", "取值为空")
            return {"type": typ, "literal": text}, issues
        if typ == "enum":
            allowed = definition.get("allowed") or []
            mapping = dict(((card or {}).get("properties", {}).get(predicate_id) or {}).get("enum_map") or {})
            text = raw.strip()
            value = text if text in allowed else _lookup({item: item for item in allowed}, text)
            if value is None:
                value = _lookup(mapping, text)
            if value is None or allowed and value not in allowed:
                raise NormalizationError("ENUM_NOT_ALLOWED", f"“{raw}”不是 {predicate_id} 的可选值（{', '.join(allowed)}）")
            return {"type": typ, "literal": value}, issues
        if typ == "boolean":
            value = _lookup(BOOLEANS, raw)
            if value is None:
                raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”无法解析为是/否")
            return {"type": typ, "literal": value}, issues
        if typ in ("date", "datetime"):
            parsed = parse_time(raw, None)
            if parsed is None:
                raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”无法解析为日期")
            if typ == "date":
                start = parsed.start.date() if isinstance(parsed.start, datetime) else parsed.start
                if parsed.precision != "day":
                    issues.append(("OTHER", "warning", f"日期精度为 {parsed.precision}，按期初日期保存"))
                return {"type": typ, "literal": start.isoformat()}, issues
            return {"type": typ, "literal": timestamp(parsed.start, self.offset)}, issues
        if typ == "node_ref":
            if not ref_id:
                raise NormalizationError("VALUE_UNPARSEABLE", "实例引用需要可解析的 value.ref")
            return {"type": typ, "literal": ref_id}, issues
        if typ in ("number", "decimal", "quantity", "currency"):
            return self._numeric(predicate, raw_value, card, storage)
        raise NormalizationError("VALUE_UNPARSEABLE", f"{predicate_id} 的取值类型 {typ} 不支持文档抽取")

    def _numeric(self, predicate, raw_value, card, storage):
        definition = predicate.get("value", {})
        typ = definition.get("type")
        raw = str(raw_value.get("raw", ""))
        issues = []
        currency = None
        text = raw
        if typ == "currency":
            aliases = dict(self.profile.get("currency_aliases") or {})
            text, found = _strip_currency(text, aliases)
            if raw_value.get("unit_raw"):
                unit_text = nfkc(raw_value["unit_raw"]).strip()
                mapped = _lookup(aliases, unit_text) or (unit_text if unit_text in self.registry.currencies else None)
                if mapped is None:
                    raise NormalizationError("UNIT_NOT_ALLOWED", f"无法识别币种写法“{unit_text}”")
                found.add(mapped)
                text, _ = _strip_currency(text, {raw_value["unit_raw"]: mapped})
            if len(found) > 1:
                raise NormalizationError("UNIT_NOT_ALLOWED", f"“{raw}”出现了多个币种")
            currency = next(iter(found), None) or definition.get("default_unit")
            if currency is None:
                raise NormalizationError("UNIT_NOT_ALLOWED", f"“{raw}”没有写明币种")
            if currency not in self.registry.currencies or definition.get("units") and currency not in definition["units"]:
                raise NormalizationError("UNIT_NOT_ALLOWED", f"币种 {currency} 未注册或不允许用于 {predicate['id']}")
        aliases = self.unit_aliases(predicate["id"], card) if typ == "quantity" else {}
        unit_words = [nfkc(word) for word in aliases] + list(self.registry.units)
        try:
            parsed = parse_number_expression(text, unit_words)
        except InvalidOperation as exc:
            raise NormalizationError("VALUE_UNPARSEABLE", f"“{raw}”无法解析为数值") from exc
        qualifiers = dict(parsed.qualifiers)
        unit = None
        factor = None
        if typ == "quantity":
            unit_text = raw_value.get("unit_raw") or parsed.unit_text
            if raw_value.get("unit_raw") and parsed.unit_text and nfkc(parsed.unit_text).strip() != nfkc(raw_value["unit_raw"]).strip():
                raise NormalizationError("UNIT_NOT_ALLOWED", f"unit_raw“{raw_value['unit_raw']}”与原文单位“{parsed.unit_text}”不一致")
            if parsed.percent:
                if "one" in (definition.get("units") or []) or definition.get("dimension") == "dimensionless":
                    unit_text = "one"
                else:
                    raise NormalizationError("UNIT_NOT_ALLOWED", f"{predicate['id']} 不接受百分比")
            unit, conversion = self._resolve_unit(predicate, unit_text, aliases)
            if conversion:
                source_unit, factor = conversion
                qualifiers["source_unit"] = source_unit
        elif parsed.unit_text and typ != "currency":
            raise NormalizationError("UNIT_NOT_ALLOWED", f"{predicate['id']} 是无单位数值，原文带有“{parsed.unit_text}”")
        elif parsed.unit_text and typ == "currency":
            raise NormalizationError("UNIT_NOT_ALLOWED", f"“{raw}”中的“{parsed.unit_text}”不是可识别的币种写法")

        def scaled(number):
            return number * factor if factor is not None and number is not None else number

        value = scaled(parsed.value)
        if parsed.minimum is not None:
            qualifiers["value_form"] = "range"
            qualifiers["range_min"] = plain(scaled(parsed.minimum))
            qualifiers["range_max"] = plain(scaled(parsed.maximum))
        if parsed.tolerance is not None:
            qualifiers["tolerance"] = plain(scaled(parsed.tolerance))
        if parsed.percent:
            qualifiers["source_form"] = "percent"
        if storage == "attr" and qualifiers:
            if set(qualifiers) & {"bound", "value_form"}:
                raise NormalizationError("VALUE_UNPARSEABLE", "属性只能保存确定值；区间或上下限请改写为断言或拆分")
            issues.append(("OTHER", "warning", "属性存储不保留约数、公差等限定条件，仅保存数值"))
            qualifiers = {}
        if typ == "number":
            result = {"type": typ, "literal": int(value) if value == value.to_integral_value() else float(value)}
        elif typ == "currency":
            minor = int(self.registry.currencies[currency].get("minor_units", 2))
            rounded = value.quantize(Decimal(1).scaleb(-minor))
            if rounded != value:
                issues.append(("OTHER", "warning", f"金额按 {currency} 的最小货币单位舍入为 {rounded}"))
            result = {"type": typ, "literal": format(rounded, "f"), "unit": currency}
        elif typ == "decimal":
            result = {"type": typ, "literal": plain(value)}
        else:
            result = {"type": typ, "literal": plain(value), "unit": unit}
        if qualifiers:
            result["qualifiers"] = qualifiers
        return result, issues

    def temporal(self, raw, reference):
        """Return (iso_timestamp_start, iso_timestamp_exclusive_end) or raise NormalizationError."""
        parsed = parse_time(raw, reference)
        if parsed is None:
            raise NormalizationError("VALUE_UNPARSEABLE", f"时间“{raw}”无法解析")
        end = parsed.end()
        return timestamp(parsed.start, self.offset), timestamp(end, self.offset)
