from abc import abstractproperty

from ..hdl import *
from ..build import *


__all__ = ["LatticeICE40Platform"]


class LatticeICE40Platform(TemplatedPlatform):
    """
    Required tools:
        * ``yosys``
        * ``nextpnr-ice40``
        * ``icepack``

    The environment is populated by running the script specified in the environment variable
    ``NMIGEN_IceStorm_env``, if present.

    Available overrides:
        * ``verbose``: enables logging of informational messages to standard error.
        * ``read_verilog_opts``: adds options for ``read_verilog`` Yosys command.
        * ``synth_opts``: adds options for ``synth_ice40`` Yosys command.
        * ``script_after_read``: inserts commands after ``read_ilang`` in Yosys script.
        * ``script_after_synth``: inserts commands after ``synth_ice40`` in Yosys script.
        * ``yosys_opts``: adds extra options for Yosys.
        * ``nextpnr_opts``: adds extra and overrides default options (``--placer heap``)
          for nextpnr.

    Build products:
        * ``{{name}}.rpt``: Yosys log.
        * ``{{name}}.json``: synthesized RTL.
        * ``{{name}}.tim``: nextpnr log.
        * ``{{name}}.asc``: ASCII bitstream.
        * ``{{name}}.bin``: binary bitstream.
    """

    toolchain = "IceStorm"

    device  = abstractproperty()
    package = abstractproperty()

    _nextpnr_device_options = {
        "iCE40LP384": "--lp384",
        "iCE40LP1K":  "--lp1k",
        "iCE40LP4K":  "--lp8k",
        "iCE40LP8K":  "--lp8k",
        "iCE40HX1K":  "--hx1k",
        "iCE40HX4K":  "--hx8k",
        "iCE40HX8K":  "--hx8k",
        "iCE40UP5K":  "--up5k",
        "iCE5LP4K":   "--u4k",
    }
    _nextpnr_package_options = {
        "iCE40LP4K":  ":4k",
        "iCE40HX4K":  ":4k",
    }

    file_templates = {
        **TemplatedPlatform.build_script_templates,
        "{{name}}.il": r"""
            # {{autogenerated}}
            {{emit_design("rtlil")}}
        """,
        "{{name}}.ys": r"""
            # {{autogenerated}}
            {% for file in platform.iter_extra_files(".v") -%}
                read_verilog {{get_override("read_opts")|options}} {{file}}
            {% endfor %}
            {% for file in platform.iter_extra_files(".sv") -%}
                read_verilog -sv {{get_override("read_opts")|options}} {{file}}
            {% endfor %}
            read_ilang {{name}}.il
            {{get_override("script_after_read")|default("# (script_after_read placeholder)")}}
            synth_ice40 {{get_override("synth_opts")|options}} -top {{name}}
            {{get_override("script_after_synth")|default("# (script_after_synth placeholder)")}}
            write_json {{name}}.json
        """,
        "{{name}}.pcf": r"""
            # {{autogenerated}}
            {% for port_name, pin_name, attrs in platform.iter_port_constraints_bits() -%}
                set_io {{port_name}} {{pin_name}}
            {% endfor %}
        """,
        "{{name}}_pre_pack.py": r"""
            # {{autogenerated}}
            {% for signal, frequency in platform.iter_clock_constraints() -%}
            {# Clock in MHz #}
            ctx.addClock("{{signal.name}}", {{frequency/1000000}})
            {% endfor%}
        """,
    }
    command_templates = [
        r"""
        {{get_tool("yosys")}}
            {{quiet("-q")}}
            {{get_override("yosys_opts")|options}}
            -l {{name}}.rpt
            {{name}}.ys
        """,
        r"""
        {{get_tool("nextpnr-ice40")}}
            {{quiet("--quiet")}}
            {{get_override("nextpnr_opts")|default(["--placer","heap"])|options}}
            --log {{name}}.tim
            {{platform._nextpnr_device_options[platform.device]}}
            --package
                {{platform.package|lower}}{{platform._nextpnr_package_options[platform.device]}}
            --json {{name}}.json
            --pcf {{name}}.pcf
            --pre-pack {{name}}_pre_pack.py
            --asc {{name}}.asc
        """,
        r"""
        {{get_tool("icepack")}}
            {{verbose("-v")}}
            {{name}}.asc
            {{name}}.bin
        """
    ]

    def should_skip_port_component(self, port, attrs, component):
        # On iCE40, a differential input is placed by only instantiating an SB_IO primitive for
        # the pin with z=0, which is the non-inverting pin. The pinout unfortunately differs
        # between LP/HX and UP series:
        #  * for LP/HX, z=0 is DPxxB   (B is non-inverting, A is inverting)
        #  * for UP,    z=0 is IOB_xxA (A is non-inverting, B is inverting)
        if attrs.get("IO_STANDARD", "SB_LVCMOS") == "SB_LVDS_INPUT" and component == "n":
            return True
        return False

    def _get_io_buffer(self, m, pin, port, attrs, i_invert=None, o_invert=None):
        def get_dff(clk, d, q):
            m.submodules += Instance("$dff",
                p_CLK_POLARITY=1,
                p_WIDTH=len(d),
                i_CLK=clk,
                i_D=d,
                o_Q=q)

        def get_ixor(y, invert):
            if invert is None:
                return y
            else:
                a = Signal.like(y, name_suffix="_x{}".format(1 if invert else 0))
                for bit in range(len(y)):
                    m.submodules += Instance("SB_LUT4",
                        p_LUT_INIT=0b01 if invert else 0b10,
                        i_I0=a[bit],
                        i_I1=Const(0),
                        i_I2=Const(0),
                        i_I3=Const(0),
                        o_O=y[bit])
                return a

        def get_oxor(a, invert):
            if invert is None:
                return a
            else:
                y = Signal.like(a, name_suffix="_x{}".format(1 if invert else 0))
                for bit in range(len(a)):
                    m.submodules += Instance("SB_LUT4",
                        p_LUT_INIT=0b01 if invert else 0b10,
                        i_I0=a[bit],
                        i_I1=Const(0),
                        i_I2=Const(0),
                        i_I3=Const(0),
                        o_O=y[bit])
                return y

        if "GLOBAL" in attrs:
            is_global_input = bool(attrs["GLOBAL"])
            del attrs["GLOBAL"]
        else:
            is_global_input = False
        assert not (is_global_input and i_invert)

        if "i" in pin.dir:
            if pin.xdr < 2:
                pin_i  = get_ixor(pin.i,  i_invert)
            elif pin.xdr == 2:
                pin_i0 = get_ixor(pin.i0, i_invert)
                pin_i1 = get_ixor(pin.i1, i_invert)
        if "o" in pin.dir:
            if pin.xdr < 2:
                pin_o  = get_oxor(pin.o,  o_invert)
            elif pin.xdr == 2:
                pin_o0 = get_oxor(pin.o0, o_invert)
                pin_o1 = get_oxor(pin.o1, o_invert)

        if "i" in pin.dir and pin.xdr == 2:
            i0_ff = Signal.like(pin_i0, name_suffix="_ff")
            i1_ff = Signal.like(pin_i1, name_suffix="_ff")
            get_dff(pin.i_clk, i0_ff, pin_i0)
            get_dff(pin.i_clk, i1_ff, pin_i1)
        if "o" in pin.dir and pin.xdr == 2:
            o1_ff = Signal.like(pin_o1, name_suffix="_ff")
            get_dff(pin.o_clk, pin_o1, o1_ff)

        for bit in range(len(port)):
            io_args = [
                ("io", "PACKAGE_PIN", port[bit]),
                *(("p", key, value) for key, value in attrs.items()),
            ]

            if "i" not in pin.dir:
                i_type =     0b00 # PIN_NO_INPUT aka PIN_INPUT_REGISTERED
            elif pin.xdr == 0:
                i_type =     0b01 # PIN_INPUT
            elif pin.xdr > 0:
                i_type =     0b00 # PIN_INPUT_REGISTERED
            if "o" not in pin.dir:
                o_type = 0b0000   # PIN_NO_OUTPUT
            elif pin.xdr == 0 and pin.dir == "o":
                o_type = 0b0110   # PIN_OUTPUT
            elif pin.xdr == 0:
                o_type = 0b1010   # PIN_OUTPUT_TRISTATE
            elif pin.xdr == 1 and pin.dir == "o":
                o_type = 0b0101   # PIN_OUTPUT_REGISTERED
            elif pin.xdr == 1:
                o_type = 0b1101   # PIN_OUTPUT_REGISTERED_ENABLE_REGISTERED
            elif pin.xdr == 2 and pin.dir == "o":
                o_type = 0b0100   # PIN_OUTPUT_DDR
            elif pin.xdr == 2:
                o_type = 0b1100   # PIN_OUTPUT_DDR_ENABLE_REGISTERED
            io_args.append(("p", "PIN_TYPE", (o_type << 2) | i_type))

            if hasattr(pin, "i_clk"):
                io_args.append(("i", "INPUT_CLK",  pin.i_clk))
            if hasattr(pin, "o_clk"):
                io_args.append(("i", "OUTPUT_CLK", pin.o_clk))

            if "i" in pin.dir:
                if pin.xdr == 0 and is_global_input:
                    io_args.append(("o", "GLOBAL_BUFFER_OUTPUT", pin.i[bit]))
                elif pin.xdr < 2:
                    io_args.append(("o", "D_IN_0",  pin_i[bit]))
                elif pin.xdr == 2:
                    # Re-register both inputs before they enter fabric. This increases hold time
                    # to an entire cycle, and adds one cycle of latency.
                    io_args.append(("o", "D_IN_0",  i0_ff))
                    io_args.append(("o", "D_IN_1",  i1_ff))
            if "o" in pin.dir:
                if pin.xdr < 2:
                    io_args.append(("i", "D_OUT_0", pin_o[bit]))
                elif pin.xdr == 2:
                    # Re-register negedge output after it leaves fabric. This increases setup time
                    # to an entire cycle, and doesn't add latency.
                    io_args.append(("i", "D_OUT_0", pin_o0[bit]))
                    io_args.append(("i", "D_OUT_1", o1_ff))

            if pin.dir in ("oe", "io"):
                io_args.append(("i", "OUTPUT_ENABLE", pin.oe))

            if is_global_input:
                m.submodules[pin.name] = Instance("SB_GB_IO", *io_args)
            else:
                m.submodules[pin.name] = Instance("SB_IO", *io_args)

    def get_input(self, pin, port, attrs, invert):
        self._check_feature("single-ended input", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        self._get_io_buffer(m, pin, port, attrs, i_invert=True if invert else None)
        return m

    def get_output(self, pin, port, attrs, invert):
        self._check_feature("single-ended output", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        self._get_io_buffer(m, pin, port, attrs, o_invert=True if invert else None)
        return m

    def get_tristate(self, pin, port, attrs, invert):
        self._check_feature("single-ended tristate", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        self._get_io_buffer(m, pin, port, attrs, o_invert=True if invert else None)
        return m

    def get_input_output(self, pin, port, attrs, invert):
        self._check_feature("single-ended input/output", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        self._get_io_buffer(m, pin, port, attrs, i_invert=True if invert else None,
                                                 o_invert=True if invert else None)
        return m

    def get_diff_input(self, pin, p_port, n_port, attrs, invert):
        self._check_feature("differential input", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        # See comment in should_skip_port_component above.
        self._get_io_buffer(m, pin, p_port, attrs, i_invert=True if invert else None)
        return m

    def get_diff_output(self, pin, p_port, n_port, attrs, invert):
        self._check_feature("differential output", pin, attrs,
                            valid_xdrs=(0, 1, 2), valid_attrs=True)
        m = Module()
        # Note that the non-inverting output pin is not driven the same way as a regular
        # output pin. The inverter introduces a delay, so for a non-inverting output pin,
        # an identical delay is introduced by instantiating a LUT. This makes the waveform
        # perfectly symmetric in the xdr=0 case.
        self._get_io_buffer(m, pin, p_port, attrs, o_invert=invert)
        self._get_io_buffer(m, pin, n_port, attrs, o_invert=not invert)
        return m

    # Tristate and bidirectional buffers are not supported on iCE40 because it requires external
    # termination, which is incompatible for input and output differential I/Os.
