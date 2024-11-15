from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import qick.asm_v2
from qcodes import Parameter

from qcodes_qick.macro_base_v2 import Macro
from qcodes_qick.parameters_v2 import SweepableOrAutoParameter

if TYPE_CHECKING:
    from qick.asm_v2 import QickParam

    from qcodes_qick.instrument_v2 import QickInstrument
    from qcodes_qick.pulse_base_v2 import DacPulse


class PlayPulse(Macro):
    """Play a pulse.

    Parameters
    ----------
    parent : QickInstrument
        Where to play the pulse.
    pulse : Pulse
        The pulse to play.
    t : float | QickParam | Literal['auto'], default='auto'
        Time relative to the last Delay or DelayAuto. 'auto' means the end of the last pulse on the DAC channel.
    """

    def __init__(
        self,
        parent: QickInstrument,
        pulse: DacPulse,
        t: float | QickParam | Literal["auto"] = "auto",
    ) -> None:
        assert pulse.parent.parent is parent
        qick_macro = qick.asm_v2.Pulse(
            ch=pulse.parent.channel_num,
            name=pulse.short_name,
            t=t * 1e6 if t != "auto" else "auto",
        )
        super().__init__(
            parent,
            "PlayPulse",
            qick_macro,
            dacs=[pulse.parent],
            envelopes=[pulse.envelope] if hasattr(pulse, "envelope") else (),
            pulses=[pulse],
        )

        self.pulse_name = Parameter(
            name="pulse_name",
            instrument=self,
            label="Pulse name",
            initial_cache_value=pulse.short_name,
        )
        self.t = SweepableOrAutoParameter(
            name="t",
            instrument=self,
            label="Time",
            unit="sec",
            initial_value=t,
            settable=False,
        )
