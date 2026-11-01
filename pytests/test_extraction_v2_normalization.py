"""Deterministic normalization of raw extracted expressions (extraction protocol v2)."""
from datetime import date
from decimal import Decimal

import pytest

from knowledge_os.normalization import NormalizationError, ValueNormalizer, parse_cn_integer, parse_number_expression, parse_time


@pytest.mark.parametrize("text,value", [("三十", 30), ("十五", 15), ("两千零五", 2005), ("三十万", 300000),
                                        ("一亿二千万", 120000000), ("二零二六", 2026)])
def test_chinese_integers(text, value):
    assert parse_cn_integer(text) == value


@pytest.mark.parametrize("raw,value,unit,qualifiers", [
    ("约30天", "30", "天", {"approximation": "approximate"}),
    ("不低于5kW", "5", "kW", {"bound": "lower"}),
    ("超过 100", "100", "", {"bound": "lower_exclusive"}),
    ("20天以上", "20", "天", {"bound": "lower"}),
    ("30天左右", "30", "天", {"approximation": "approximate"}),
    ("1.2亿", "120000000", "", {}),
    ("1,234.5", "1234.5", "", {}),
    ("两千瓦", "2", "千瓦", {}),
    ("5 千瓦", "5", "千瓦", {}),
])
def test_number_expressions(raw, value, unit, qualifiers):
    parsed = parse_number_expression(raw, ["千瓦", "天", "kW"])
    assert parsed.value == Decimal(value) and parsed.unit_text == unit and parsed.qualifiers == qualifiers


def test_ranges_tolerance_and_percent():
    ranged = parse_number_expression("10-20天", ["天"])
    assert (ranged.minimum, ranged.maximum, ranged.unit_text) == (10, 20, "天")
    assert parse_number_expression("10±0.5").tolerance == Decimal("0.5")
    assert parse_number_expression("30%").value == Decimal("0.3")
    assert parse_number_expression("百分之三十").percent
    for bad in ("约", "10天到5天", "3和4"):
        with pytest.raises(NormalizationError):
            parse_number_expression(bad, ["天"])


@pytest.mark.parametrize("raw,start,precision", [
    ("2026年10月15日", date(2026, 10, 15), "day"), ("2026-10-15", date(2026, 10, 15), "day"),
    ("2026/1/5", date(2026, 1, 5), "day"), ("2026年10月", date(2026, 10, 1), "month"),
    ("2026年三季度", date(2026, 7, 1), "quarter"), ("2026Q3", date(2026, 7, 1), "quarter"),
    ("2026年", date(2026, 1, 1), "year"), ("昨天", date(2026, 10, 14), "day"), ("上月", date(2026, 9, 1), "month"),
    ("去年", date(2025, 1, 1), "year"), ("3天后", date(2026, 10, 18), "day"), ("10月1日", date(2026, 10, 1), "day"),
])
def test_time_expressions_use_the_document_reference_date(raw, start, precision):
    parsed = parse_time(raw, date(2026, 10, 15))
    assert (parsed.start, parsed.precision) == (start, precision)


def test_relative_time_without_reference_is_unparseable():
    assert parse_time("昨天", None) is None and parse_time("不是日期", date(2026, 1, 1)) is None


def test_typed_value_normalization_against_registry(service):
    assets = service.registry.extraction_v2
    normalizer = ValueNormalizer(service.registry, assets["profile"])
    product, project = assets["cards"]["product"], assets["cards"]["project"]
    assert normalizer.normalize("lead_time_days", {"raw": "约30天", "unit_raw": "天"}, card=product)[0] == {
        "type": "quantity", "literal": "30", "unit": "calendar_days", "qualifiers": {"approximation": "approximate"}}
    assert normalizer.normalize("rated_power", {"raw": "5千瓦"})[0] == {"type": "quantity", "literal": "5", "unit": "kW"}
    assert normalizer.normalize("acquisition_cost", {"raw": "人民币5万元"})[0] == {"type": "currency", "literal": "50000.00", "unit": "CNY"}
    value, issues = normalizer.normalize("acquisition_cost", {"raw": "$1,200.505"})
    assert value["literal"] == "1200.50" and value["unit"] == "USD" and issues
    assert normalizer.normalize("utilization_factor", {"raw": "85%"})[0]["literal"] == "0.85"
    assert normalizer.normalize("useful_life_hours", {"raw": "3万小时"})[0] == {"type": "quantity", "literal": "30000", "unit": "hour"}
    assert normalizer.normalize("risk_level", {"raw": "高风险"}, card=project)[0] == {"type": "enum", "literal": "high"}
    assert normalizer.normalize("legal_name", {"raw": " Acme Ltd. "})[0] == {"type": "string", "literal": "Acme Ltd."}
    ranged = normalizer.normalize("rated_power", {"raw": "10-20kW"})[0]
    assert ranged["qualifiers"] == {"value_form": "range", "range_min": "10", "range_max": "20"}
    for predicate, raw, code in (("risk_level", "爆表", "ENUM_NOT_ALLOWED"), ("acquisition_cost", "1200", "UNIT_NOT_ALLOWED"),
                                 ("rated_power", "5 马力", "UNIT_NOT_ALLOWED"), ("rated_power", "很大", "VALUE_UNPARSEABLE"),
                                 ("lead_time_days", "30天", "UNIT_NOT_ALLOWED")):
        with pytest.raises(NormalizationError) as error:
            normalizer.normalize(predicate, {"raw": raw}, card=project)
        assert error.value.code == code, (predicate, raw)
    start, end = normalizer.temporal("2026年三季度", date(2026, 10, 15))
    assert (start, end) == ("2026-07-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00")
