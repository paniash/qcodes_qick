from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from qcodes_qick.channels import AdcChannel, DacChannel
from qcodes_qick.parameters import (
    GainParameter,
    HzParameter,
    SecParameter,
    TProcSecParameter,
)
from qcodes_qick.protocol_base import HardwareSweep, NDAveragerProtocol
from qick.averager_program import NDAveragerProgram, QickSweep
from qick.qick_asm import QickConfig

if TYPE_CHECKING:
    from qcodes_qick.instruments import QickInstrument


class S21Protocol(NDAveragerProtocol):

    def __init__(
        self,
        parent: QickInstrument,
        dac: DacChannel,
        adc: AdcChannel,
        name="S21Protocol",
        **kwargs,
    ):
        super().__init__(parent, name, **kwargs)
        self.dac = dac
        self.adc = adc
        self.dac.matching_adc.set(adc.channel)
        self.adc.matching_dac.set(dac.channel)

        self.pulse_gain = GainParameter(
            name="pulse_gain",
            instrument=self,
            label="Pulse gain",
            initial_value=0.5,
        )

        self.pulse_freq = HzParameter(
            name="pulse_freq",
            instrument=self,
            label="Pulse frequency",
            initial_value=1e9,
            channel=self.dac,
        )

        self.pulse_length = SecParameter(
            name="pulse_length",
            instrument=self,
            label="Pulse length",
            initial_value=10e-6,
            channel=self.dac,
        )

        self.adc_trig_offset = TProcSecParameter(
            name="adc_trig_offset",
            instrument=self,
            label="Delay between sending probe pulse and ADC initialization",
            initial_value=0,
            qick_instrument=self.parent,
        )

        self.relax_delay = TProcSecParameter(
            name="relax_delay",
            instrument=self,
            label="Delay between reps",
            initial_value=1e-3,
            qick_instrument=self.parent,
        )

        self.adc_length = SecParameter(
            name="adc_length",
            instrument=self,
            label="Length of ADC acquisition window",
            initial_value=10e-6,
            channel=self.adc,
        )

    def generate_program(self, soccfg: QickConfig, cfg: dict) -> S21Program:
        return S21Program(soccfg, cfg)


class S21Program(NDAveragerProgram):

    def initialize(self):
        p: S21Protocol = self.cfg["protocol"]
        hardware_sweeps: Sequence[HardwareSweep] = self.cfg.get("hardware_sweeps", ())

        self.declare_gen(
            ch=p.dac.channel,
            nqz=p.dac.nqz.get(),
        )
        self.declare_readout(
            ch=p.adc.channel,
            length=p.adc_length.get_raw(),
            sel="product",
            freq=p.pulse_freq.get() / 1e6,
        )
        self.set_pulse_registers(
            ch=p.dac.channel,
            style="const",
            freq=p.pulse_freq.get_raw(),
            phase=0,
            gain=p.pulse_gain.get_raw(),
            phrst=0,
            stdysel="zero",
            mode="oneshot",
            length=p.pulse_length.get_raw(),
        )

        for sweep in reversed(hardware_sweeps):
            if sweep.parameter is p.pulse_gain:
                reg = self.get_gen_reg(p.dac.channel, "gain")
                self.add_sweep(
                    QickSweep(self, reg, sweep.start_int, sweep.stop_int, sweep.num)
                )
            else:
                raise NotImplementedError

        self.synci(200)  # Give processor some time to configure pulses

    def body(self):
        p: S21Protocol = self.cfg["protocol"]

        self.measure(
            adcs=[p.adc.channel],
            pulse_ch=p.dac.channel,
            adc_trig_offset=p.adc_trig_offset.get_raw(),
            t="auto",
            wait=True,
            syncdelay=p.relax_delay.get_raw(),
        )