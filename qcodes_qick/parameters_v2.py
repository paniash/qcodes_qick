from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from qcodes import ManualParameter
from qcodes.instrument import InstrumentModule
from qcodes.validators import Enum, MultiType, Numbers, Validator
from qick.asm_v2 import QickParam

if TYPE_CHECKING:
    from qcodes.instrument import InstrumentBase

    from qcodes_qick.instrument_v2 import QickInstrument


class SweepableNumbers(Validator):
    def __init__(
        self,
        min_value: float = -float("inf"),
        max_value: float = float("inf"),
    ) -> None:
        self.numbers = Numbers(min_value, max_value)
        self._valid_values = (min_value, max_value)

    def validate(self, value: float | QickParam, context: str = ""):
        if isinstance(value, QickParam):
            self.numbers.validate(value.minval(), context)
            self.numbers.validate(value.maxval(), context)
        else:
            self.numbers.validate(value, context)


class SweepableParameter(ManualParameter):
    def __init__(
        self,
        name: str,
        instrument: InstrumentBase,
        label: str,
        unit: str,
        initial_value: float,
        min_value: float = -float("inf"),
        max_value: float = float("inf"),
        settable: bool = True,
        **kwargs,
    ) -> None:
        inst = instrument
        while isinstance(inst, InstrumentModule):
            inst = inst.parent
        self.qick_instrument: QickInstrument = inst
        super().__init__(
            name,
            instrument,
            label=label,
            unit=unit,
            set_parser=self.set_parser,
            vals=SweepableNumbers(min_value, max_value),
            initial_value=initial_value,
            **kwargs,
        )
        self._settable = settable

    def set_parser(self, value: float | QickParam) -> float | QickParam:
        # keep track of all swept parameters of the instrument
        if isinstance(value, QickParam):
            self.qick_instrument.swept_params.add(self)
        elif self in self.qick_instrument.swept_params:
            self.qick_instrument.swept_params.remove(self)
        return value


class SweepableOrAutoParameter(ManualParameter):
    def __init__(
        self,
        name: str,
        instrument: InstrumentBase,
        label: str,
        unit: str,
        initial_value: float,
        min_value: float = -float("inf"),
        max_value: float = float("inf"),
        settable: bool = True,
        **kwargs,
    ) -> None:
        inst = instrument
        while isinstance(inst, InstrumentModule):
            inst = inst.parent
        self.qick_instrument: QickInstrument = inst
        super().__init__(
            name,
            instrument,
            label=label,
            unit=unit,
            set_parser=self.set_parser,
            vals=MultiType(SweepableNumbers(min_value, max_value), Enum("auto")),
            initial_value=initial_value,
            **kwargs,
        )
        self._settable = settable

    def set_parser(
        self, value: float | QickParam | Literal["auto"]
    ) -> float | QickParam | Literal["auto"]:
        # keep track of all swept parameters of the instrument
        if isinstance(value, QickParam):
            self.qick_instrument.swept_params.add(self)
        elif self in self.qick_instrument.swept_params:
            self.qick_instrument.swept_params.remove(self)
        return value
