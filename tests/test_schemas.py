from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from ai_billing.schemas import CostBreakdown, Usage


class TestUsage:
    def test_required_fields(self):
        u = Usage(input_tokens=100, output_tokens=50)
        assert u.input_tokens == 100
        assert u.output_tokens == 50
        assert u.cached_input_tokens == 0
        assert u.cache_write_tokens == 0

    def test_all_fields(self):
        u = Usage(
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=20,
            cache_write_tokens=10,
        )
        assert u.cached_input_tokens == 20
        assert u.cache_write_tokens == 10

    def test_frozen(self):
        u = Usage(input_tokens=100, output_tokens=50)
        with pytest.raises(FrozenInstanceError):
            u.input_tokens = 999  # type: ignore[misc]


class TestCostBreakdown:
    def test_construction(self):
        cb = CostBreakdown(
            cost_no_vat=Decimal("6.000000"),
            vat=Decimal("1.200000"),
            cost_total=Decimal("7.200000"),
            by_component={
                "input": Decimal("1.000000"),
                "output": Decimal("5.000000"),
            },
        )
        assert cb.cost_no_vat == Decimal("6.000000")
        assert cb.cost_total == cb.cost_no_vat + cb.vat
        assert cb.by_component["input"] == Decimal("1.000000")

    def test_frozen(self):
        cb = CostBreakdown(
            cost_no_vat=Decimal("0"),
            vat=Decimal("0"),
            cost_total=Decimal("0"),
            by_component={},
        )
        with pytest.raises(FrozenInstanceError):
            cb.cost_no_vat = Decimal("1")  # type: ignore[misc]

    def test_default_by_component_empty(self):
        cb = CostBreakdown(
            cost_no_vat=Decimal("0"),
            vat=Decimal("0"),
            cost_total=Decimal("0"),
        )
        assert cb.by_component == {}
