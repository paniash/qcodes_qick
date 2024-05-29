from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional, Sequence, Union

import numpy as np
from qcodes import ManualParameter, Measurement, Parameter
from qcodes.instrument import InstrumentModule
from qcodes.validators import Ints
from qick.asm_v2 import AveragerProgramV2
from qick.qick_asm import AcquireMixin
from tqdm.contrib.itertools import product as tqdm_product

from qcodes_qick.instruction_base_v2 import QickInstruction
from qcodes_qick.parameters import TProcSecParameter

if TYPE_CHECKING:
    from qick.qick_asm import QickConfig

    from qcodes_qick.channels import AdcChannel, DacChannel
    from qcodes_qick.instruments import QickInstrument
    from qcodes_qick.parameters import HardwareParameter


class QickProtocol(InstrumentModule):
    parent: QickInstrument

    def __init__(self, parent: QickInstrument, name: str, **kwargs):
        super().__init__(parent, name, **kwargs)
        self.instructions: Sequence[QickInstruction] = []
        assert parent.tproc_version.get() == 2
        parent.add_submodule(name, self)


class SoftwareSweep:
    parameters: Sequence[Parameter]
    values: Sequence[float]

    def __init__(
        self,
        parameters: Parameter | Sequence[Parameter],
        start: Union[float, Sequence[float]],
        stop: Optional[float] = None,
        num: Optional[int] = None,
        skip_first: bool = False,
        skip_last: bool = False,
    ):
        if isinstance(parameters, Parameter):
            self.parameters = [parameters]
        else:
            self.parameters = parameters

        # make sure that all parameters have the same unit
        assert len({parameter.unit for parameter in self.parameters}) == 1

        if isinstance(start, Sequence):
            self.values = start
        else:
            self.values = np.linspace(start, stop, num)
        if skip_first:
            self.values = self.values[1:]
        if skip_last:
            self.values = self.values[:-1]


class HardwareSweep:
    def __init__(
        self,
        parameter: HardwareParameter,
        start: float,
        stop: float,
        num: int,
        skip_first: bool = False,
        skip_last: bool = False,
    ):
        self.parameter = parameter

        self.step_int = parameter.float2int((stop - start) / (num - 1))
        start_int = parameter.float2int(start)
        self.values_int = start_int + self.step_int * np.arange(num, dtype=np.int64)
        if skip_first:
            self.values_int = self.values_int[1:]
        if skip_last:
            self.values_int = self.values_int[:-1]
        self.start_int = self.values_int[0]
        self.stop_int = self.values_int[-1]
        self.num = len(self.values_int)

        self.start = parameter.int2float(self.start_int)
        self.stop = parameter.int2float(self.stop_int)
        self.step = parameter.int2float(self.step_int)
        self.values = np.array([parameter.int2float(i) for i in self.values_int])


class SweepProtocol(ABC, QickProtocol):
    def __init__(
        self,
        parent: QickInstrument,
        name: str,
        **kwargs,
    ):
        super().__init__(parent, name, **kwargs)

        self.hard_avgs = ManualParameter(
            name="hard_avgs",
            instrument=self,
            label="Number of hardware repetitions to average over",
            vals=Ints(min_value=0),
            initial_value=1000,
        )
        self.soft_avgs = ManualParameter(
            name="soft_avgs",
            instrument=self,
            label="Number of software repetitions to average over",
            vals=Ints(min_value=0),
            initial_value=1,
        )
        self.final_delay = TProcSecParameter(
            name="final_delay",
            instrument=self,
            label="Delay time to add at the end of the shot timeline, after the end of the last pulse or readout",
            initial_value=1e-6,
            qick_instrument=self.parent,
        )
        self.final_wait = TProcSecParameter(
            name="final_wait",
            instrument=self,
            label="Amount of time to pause tProc execution at the end of each shot, after the end of the last readout",
            initial_value=0,
            qick_instrument=self.parent,
        )
        self.initial_delay = TProcSecParameter(
            name="initial_delay",
            instrument=self,
            label="Delay time to add to the timeline before starting to run the loops, to allow enough time for tProc to execute your initialization commands",
            initial_value=1e-6,
            qick_instrument=self.parent,
        )

    @abstractmethod
    def generate_program(
        self,
        soccfg: QickConfig,
        hardware_sweeps: Sequence[HardwareSweep] = (),
    ) -> SweepProgram: ...

    def run(
        self,
        meas: Measurement,
        software_sweeps: Sequence[SoftwareSweep] = (),
        hardware_sweeps: Sequence[HardwareSweep] = (),
    ) -> int:
        # Initialize and register the sweep parameters
        setpoints = []
        for sweep in software_sweeps:
            for parameter in sweep.parameters:
                parameter.set(sweep.values[0])
                setpoints.append(parameter)
                meas.register_parameter(parameter, paramtype="array")
        for sweep in hardware_sweeps:
            sweep.parameter.set(sweep.values[0])
            setpoints.append(sweep.parameter)
            meas.register_parameter(sweep.parameter, paramtype="array")

        # instantiate the program just to obtain the ADC channel numbers and the number of readouts per experiment
        program = self.generate_program(self.parent.soccfg)
        adc_channel_nums = program.ro_chs.keys()
        readouts_per_experiment = program.reads_per_shot
        assert len(adc_channel_nums) == len(readouts_per_experiment)
        assert sum(readouts_per_experiment) > 0

        # create and register the parameters representing the acquired data
        iq_parameters = []
        for i, channel_num in enumerate(adc_channel_nums):
            for readout_number in range(readouts_per_experiment[i]):
                name = "iq"
                if readouts_per_experiment[i] > 1:
                    name += f"{readout_number}"
                if len(adc_channel_nums) > 1:
                    name += f"_ch{channel_num}"
                iq_parameters.append(Parameter(name))
                meas.register_parameter(
                    iq_parameters[-1], setpoints=setpoints, paramtype="array"
                )

        with meas.run() as datasaver:
            if len(software_sweeps) == 0:
                result = self.run_hardware_sweeps(
                    hardware_sweeps, iq_parameters, progress=True
                )
                datasaver.add_result(*result)
            else:
                soft_sweep_values = [sweep.values for sweep in software_sweeps]
                for current_values in tqdm_product(*soft_sweep_values):
                    for sweep, value in zip(software_sweeps, current_values):
                        for parameter in sweep.parameters:
                            parameter.set(value)
                    result = self.run_hardware_sweeps(
                        hardware_sweeps, iq_parameters, progress=False
                    )
                    for sweep, value in zip(software_sweeps, current_values):
                        for parameter in sweep.parameters:
                            result.append((parameter, value))
                    datasaver.add_result(*result)

        return datasaver.run_id

    def run_hardware_sweeps(
        self,
        hardware_sweeps: Sequence[HardwareSweep],
        iq_parameters: Sequence[Parameter],
        progress: bool = True,
    ):
        program = self.generate_program(self.parent.soccfg, hardware_sweeps)

        result = []
        iq = AcquireMixin.acquire(
            self=program,
            soc=self.parent.soc,
            soft_avgs=self.soft_avgs.get(),
            progress=progress,
        )
        iq = np.concatenate(iq).dot([1, 1j])
        for parameter, value in zip(iq_parameters, iq):
            result.append((parameter, value))

        sweep_values = [sweep.values for sweep in hardware_sweeps]
        sweep_coordinates = np.meshgrid(*sweep_values, indexing="ij")
        for sweep, value in zip(hardware_sweeps, sweep_coordinates):
            result.append((sweep.parameter, value))

        return result


class SweepProgram(AveragerProgramV2):
    def __init__(
        self,
        soccfg: QickConfig,
        protocol: SweepProtocol,
        hardware_sweeps: Sequence[HardwareSweep] = (),
    ):
        self.protocol = protocol
        self.hardware_sweeps = hardware_sweeps
        self.dacs: set[DacChannel] = set().union(
            *(instruction.dacs for instruction in self.protocol.instructions)
        )
        self.adcs: set[AdcChannel] = set().union(
            *(instruction.adcs for instruction in self.protocol.instructions)
        )
        super().__init__(
            soccfg,
            reps=protocol.hard_avgs.get(),
            final_delay=protocol.final_delay.get() * 1e6,
            final_wait=protocol.final_wait.get() * 1e6,
            initial_delay=protocol.initial_delay.get() * 1e6,
        )

    def initialize(self, cfg: dict):
        for dac in self.dacs:
            dac.initialize(self)
        for adc in self.adcs:
            adc.initialize(self)

        for instruction in set(self.protocol.instructions):
            instruction.initialize(self)

        for sweep in self.hardware_sweeps:
            if isinstance(sweep.parameter.instrument, QickInstruction):
                sweep.parameter.instrument.add_sweep(self, sweep)
            else:
                raise NotImplementedError(
                    f"cannot perform a hardware sweep over {sweep.parameter.name}"
                )


class SimpleSweepProtocol(SweepProtocol):
    def __init__(
        self,
        parent: QickInstrument,
        instructions: Sequence[QickInstruction],
        name="SimpleSweepProtocol",
        **kwargs,
    ):
        super().__init__(parent, name, **kwargs)
        self.instructions = instructions

    def generate_program(
        self, soccfg: QickConfig, hardware_sweeps: Sequence[HardwareSweep] = ()
    ):
        return SimpleSweepProgram(soccfg, self, hardware_sweeps)


class SimpleSweepProgram(SweepProgram):
    protocol: SimpleSweepProtocol

    def body(self, cfg: dict):
        for instruction in self.protocol.instructions:
            instruction.play(self)