from .. import *


__all__ = ["MultiReg", "ResetSynchronizer"]


class MultiReg(Elaboratable):
    """Resynchronise a signal to a different clock domain.

    Consists of a chain of flip-flops. Eliminates metastabilities at the output, but provides
    no other guarantee as to the safe domain-crossing of a signal.

    Parameters
    ----------
    i : Signal(), in
        Signal to be resynchronised
    o : Signal(), out
        Signal connected to synchroniser output
    odomain : str
        Name of output clock domain
    n : int
        Number of flops between input and output.
    reset : int
        Reset value of the flip-flops. On FPGAs, even if ``reset_less`` is True, the MultiReg is
        still set to this value during initialization.
    reset_less : bool
        If True (the default), this MultiReg is unaffected by ``odomain`` reset.
        See "Note on Reset" below.

    Platform override
    -----------------
    Define the ``get_multi_reg`` platform method to override the implementation of MultiReg,
    e.g. to instantiate library cells directly.

    Note on Reset
    -------------
    MultiReg is non-resettable by default. Usually this is the safest option; on FPGAs
    the MultiReg will still be initialized to its ``reset`` value when the FPGA loads its
    configuration.

    However, in designs where the value of the MultiReg must be valid immediately after reset,
    consider setting ``reset_less`` to False if any of the following is true:

    - You are targeting an ASIC, or an FPGA that does not allow arbitrary initial flip-flop states;
    - Your design features warm (non-power-on) resets of ``odomain``, so the one-time
      initialization at power on is insufficient;
    - Your design features a sequenced reset, and the MultiReg must maintain its reset value until
      ``odomain`` reset specifically is deasserted.

    MultiReg is reset by the ``odomain`` reset only.
    """
    def __init__(self, i, o, odomain="sync", n=2, reset=0, reset_less=True):
        self.i = i
        self.o = o
        self.odomain = odomain

        self._regs = [Signal(self.i.shape(), name="cdc{}".format(i), reset=reset,
                             reset_less=reset_less)
                      for i in range(n)]

    def elaborate(self, platform):
        if hasattr(platform, "get_multi_reg"):
            return platform.get_multi_reg(self)

        m = Module()
        for i, o in zip((self.i, *self._regs), self._regs):
            m.d[self.odomain] += o.eq(i)
        m.d.comb += self.o.eq(self._regs[-1])
        return m


class ResetSynchronizer(Elaboratable):
    def __init__(self, arst, domain="sync", n=2):
        self.arst = arst
        self.domain = domain

        self._regs = [Signal(1, name="arst{}".format(i), reset=1)
                      for i in range(n)]

    def elaborate(self, platform):
        if hasattr(platform, "get_reset_sync"):
            return platform.get_reset_sync(self)

        m = Module()
        for i, o in zip((Const(0, 1), *self._regs), self._regs):
            m.submodules += Instance("$adff",
                p_CLK_POLARITY=1,
                p_ARST_POLARITY=1,
                p_ARST_VALUE=Const(1, 1),
                p_WIDTH=1,
                i_CLK=ClockSignal(self.domain),
                i_ARST=self.arst,
                i_D=i,
                o_Q=o
            )
        m.d.comb += ResetSignal(self.domain).eq(self._regs[-1])
        return m
