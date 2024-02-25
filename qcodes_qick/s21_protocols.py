import itertools

import numpy as np
from qcodes.instrument import ManualParameter
from qcodes.utils.validators import Ints
from tqdm.auto import tqdm

from measurements.Protocols import Protocol
from qcodes_qick.channels import AdcChannel, DacChannel
from qcodes_qick.parameters import (
    DegParameter,
    GainParameter,
    HzParameter,
    SecParameter,
    TProcSecParameter,
)
from qick import QickConfig
from qick.asm_v1 import FullSpeedGenManager
from qick.averager_program import NDAveragerProgram, QickSweep


class S21Protocol(Protocol):

    def __init__(
        self, dac_channel: DacChannel, adc_channel: AdcChannel, name="S21Protocol"
    ):
        super().__init__(name)

        self.pulse_gain = GainParameter(
            name="pulse_gain",
            instrument=self,
            label="DAC gain",
            initial_value=0.5,
        )

        self.pulse_freq = HzParameter(
            name="pulse_freq",
            instrument=self,
            label="Pulse frequency",
            initial_value=1e9,
            channel=dac_channel,
        )

        self.pulse_phase = DegParameter(
            name="pulse_phase",
            instrument=self,
            label="Pulse phase",
            initial_value=0,
            channel=dac_channel,
        )

        self.pulse_length = SecParameter(
            name="pulse_length",
            instrument=self,
            label="Pulse length",
            initial_value=10e-6,
            channel=dac_channel,
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

        self.readout_length = SecParameter(
            name="readout_length",
            instrument=self,
            label="Length of the readout",
            initial_value=10e-6,
            channel=adc_channel,
        )

        self.reps = ManualParameter(
            name="reps",
            instrument=self,
            label="Measurement repetitions",
            vals=Ints(min_value=0),
            initial_value=1000,
        )

    def run_program(self, soc, cfg: dict[str, float]):
        """
        This method runs the program and returns the measurement
        result. For the NDSweep program, combining both multiple
        hardware sweeps and software sweeps, this method is implemented
        fully in the protocol. For RAveragerprograms, see Protocol.run_hybrid_loop_program

        Return:
            expt_pts:
            list of arrays containing the coordinate values of
            each variable for each measurement point
            avg_q:
            ND-array of avg_q values containing each measurement q value.
            avg_i:
            ND-array of avg_i values containing each measurement i value.
        """
        soccfg = QickConfig(soc.get_cfg())
        software_iterators = {}
        iterations = 1

        for parameter_name, value in cfg.items():
            if type(value) == list:
                software_iterators[parameter_name] = np.linspace(
                    value[0], value[1], value[2]
                ).tolist()
                iterations = iterations * value[2]

        if len(software_iterators) == 0:
            program = HardwareSweepProgram(soccfg, cfg)
            expt_pts, avg_i, avg_q = program.acquire(soc, progress=True)
            expt_pts, avg_i, avg_q = self.handle_hybrid_loop_output(
                expt_pts, avg_i, avg_q
            )
            for i in range(len(list(cfg["sweep_variables"]))):
                if list(cfg["sweep_variables"])[i] == "probe_length":
                    length_expt_pts = expt_pts[i]
                    mode_code = length_expt_pts[0] - soccfg.us2cycles(
                        cfg["sweep_variables"]["probe_length"][0]
                    )
                    f = lambda x: soccfg.cycles2us(x - mode_code)
                    fixed_length_vals = [f(x) for x in length_expt_pts]
                    expt_pts[i] = fixed_length_vals

            for i in range(avg_i.ndim - len(cfg["sweep_variables"])):
                avg_i = np.squeeze(avg_i.flatten())
                avg_q = np.squeeze(avg_q.flatten())

            return expt_pts, avg_i, avg_q

        else:

            iteratorlist = list(software_iterators)
            hardware_loop_dim = len((cfg["sweep_variables"]))

            total_hardware_sweep_points = 1
            for sweep_var in cfg["sweep_variables"]:
                total_hardware_sweep_points = (
                    total_hardware_sweep_points * cfg["sweep_variables"][sweep_var][2]
                )

            software_expt_data = [[] for i in range(len(software_iterators))]
            hardware_expt_data = [[] for i in range(hardware_loop_dim)]
            i_data = []
            q_data = []

            for coordinate_point in tqdm(
                itertools.product(*list(software_iterators.values())), total=iterations
            ):

                for coordinate_index in range(len(coordinate_point)):
                    cfg[iteratorlist[coordinate_index]] = coordinate_point[
                        coordinate_index
                    ]

                program = HardwareSweepProgram(soccfg, cfg)
                expt_pts, avg_i, avg_q = program.acquire(soc)

                for i in range(hardware_loop_dim):
                    if list(cfg["sweep_variables"])[i] == "probe_length":
                        length_expt_pts = expt_pts[i]
                        mode_code = length_expt_pts[0] - soccfg.us2cycles(
                            cfg["sweep_variables"]["probe_length"][0]
                        )
                        f = lambda x: soccfg.cycles2us(x - mode_code)
                        fixed_length_vals = [f(x) for x in length_expt_pts]
                        expt_pts[i] = fixed_length_vals
                    else:
                        expt_pts[i] = expt_pts[i].tolist()

                expt_pts, avg_i, avg_q = self.handle_hybrid_loop_output(
                    expt_pts, avg_i, avg_q
                )

                i_data.extend(avg_i.flatten())
                q_data.extend(avg_q.flatten())

                for i in range(hardware_loop_dim):
                    hardware_expt_data[i].extend(expt_pts[i])
                for i in range(len(software_iterators)):
                    software_expt_data[i].extend(
                        [
                            coordinate_point[i]
                            for x in range(total_hardware_sweep_points)
                        ]
                    )

        software_expt_data.reverse()
        software_expt_data.extend(hardware_expt_data)

        return software_expt_data, i_data, q_data


class HardwareSweepProgram(NDAveragerProgram):
    """
    This class performs a hardware loop sweep over one or more registers
    in the board. The limit is seven registers.


    Methods
    -------
    initialize(self):
        Initializes the program and defines important variables and registers.
        The sweeps are defined by self.add_sweep calls.
    body(self):
        Defines the structure of the actual measurement and will be looped over reps times.
    """

    def initialize(self):
        """
        Initialization of the qick program, and configuration of the ND-sweeps is performed in this method.
        """

        cfg = self.cfg

        # defining local variables.
        probe_ch = cfg["probe_ch"]
        freq = self.freq2reg(cfg["probe_freq"], gen_ch=probe_ch, ro_ch=cfg["ro_ch"])
        phase = self.deg2reg(cfg["probe_phase"], gen_ch=probe_ch)
        gain = round(cfg["probe_gain"])
        length = self.us2cycles(cfg["probe_length"], gen_ch=cfg["probe_ch"])
        sweep_variables = cfg["sweep_variables"]

        # Declare signal generators and readout
        self.declare_gen(ch=cfg["probe_ch"], nqz=cfg["probe_nqz"], ro_ch=cfg["ro_ch"])
        self.declare_readout(
            ch=cfg["ro_ch"],
            length=self.us2cycles(cfg["readout_length"]),
            freq=cfg["probe_freq"],
            gen_ch=cfg["probe_ch"],
        )

        self.set_pulse_registers(
            ch=probe_ch, style="const", freq=freq, phase=phase, gain=gain, length=length
        )

        for sweep_variable in sweep_variables:
            if sweep_variable == "probe_length":

                # Getting the gen manager for calculating the correct start and end points of the mode register.
                # Thus, by utilizing these methods you may ensure that you will not sent an improper mode register.
                gen_manager = FullSpeedGenManager(self, cfg["probe_ch"])
                sweep_settings = sweep_variables[sweep_variable]
                start_length = self.us2cycles(sweep_settings[0])
                end_length = self.us2cycles(sweep_settings[1])
                start_code = gen_manager.get_mode_code(
                    length=start_length, outsel="dds"
                )
                end_code = gen_manager.get_mode_code(length=end_length, outsel="dds")

                # The register containing the pulse length as the last 16 bits is referred to as the "mode" register.
                sweep_register = self.get_gen_reg(cfg["probe_ch"], "mode")
                self.add_sweep(
                    QickSweep(
                        self, sweep_register, start_code, end_code, sweep_settings[2]
                    )
                )
            else:
                sweep_settings = sweep_variables[sweep_variable]
                sweep_register = self.get_gen_reg(
                    cfg["probe_ch"], sweep_variable.replace("probe_", "")
                )
                self.add_sweep(
                    QickSweep(
                        self,
                        sweep_register,
                        sweep_settings[0],
                        sweep_settings[1],
                        sweep_settings[2],
                    )
                )

        self.synci(200)  # Give processor some time to configure pulses

    def body(self):
        """
        The main structure of the measurement is just the measurement,
        but the add_sweep commands in the initialize method add inner loops
        into the qick program instructions.
        """
        cfg = self.cfg

        self.measure(
            pulse_ch=cfg["probe_ch"],
            adcs=[cfg["ro_ch"]],
            adc_trig_offset=round(cfg["adc_trig_offset"]),
            wait=True,
            syncdelay=self.us2cycles(cfg["relax_delay"]),
        )
