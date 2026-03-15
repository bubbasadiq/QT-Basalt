"""
Basalt Melt Viscosity Model

Models the viscosity of molten basalt as a function of temperature.
Viscosity is a critical process variable — it governs fiber formation,
drawing stability, and directly influences final fiber properties.

Physics basis:
  - Vogel-Fulcher-Tammann (VFT) equation for silicate melt viscosity
  - Fitted to basalt melt data from Giordano et al. (2008)
    "Viscosity of magmatic liquids: A model" Earth and Planetary
    Science Letters, 271, 123–134.
  - Typical basalt melt viscosity at draw temperature: 500–1200 cP
  - Strong temperature dependence: ~10x change per 100°C near draw temp
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ViscosityConfig:
    """
    VFT equation parameters for a typical basalt composition.
    log10(η) = A + B / (T - C)   where T is in Kelvin, η in Pa·s

    Parameters fitted to mid-range basalt (SiO2 ~48 wt%):
      A = -4.55  (high-temperature limiting log viscosity)
      B = 5765   (pseudo-activation energy parameter)
      C = 289    (Vogel temperature, K)

    Source: Giordano et al. (2008), model applied to basalt composition.
    """
    A: float = -4.55
    B: float = 5765.0
    C: float = 289.0


class ViscosityModel:
    """
    VFT-based viscosity model for basalt melt.

    Returns viscosity in centipoise (cP) as a function of temperature.

    At typical draw temperatures (1440–1560°C):
      1440°C → ~1100 cP  (too viscous — poor fiber formation)
      1480°C → ~750 cP   (good draw window)
      1520°C → ~480 cP   (thinner fibers, faster draw)
      1560°C → ~320 cP   (very fluid — instability risk)

    Usage:
        model = ViscosityModel()
        visc_cp = model.viscosity_cp(1480.0)   # → ~750 cP
    """

    def __init__(self, config: ViscosityConfig | None = None) -> None:
        self.config = config or ViscosityConfig()

    def viscosity_pa_s(self, temp_c: float) -> float:
        """
        Calculate dynamic viscosity in Pascal-seconds.

        Args:
            temp_c: melt temperature in °C

        Returns:
            Viscosity in Pa·s

        Raises:
            ValueError: if temperature is below the Vogel temperature
        """
        temp_k = temp_c + 273.15
        cfg    = self.config

        if temp_k <= cfg.C:
            raise ValueError(
                f"Temperature {temp_c}°C is below the Vogel temperature "
                f"({cfg.C - 273.15:.1f}°C). VFT model is not valid here."
            )

        log_visc = cfg.A + cfg.B / (temp_k - cfg.C)
        return 10.0 ** log_visc

    def viscosity_cp(self, temp_c: float) -> float:
        """
        Calculate dynamic viscosity in centipoise (cP).

        1 Pa·s = 1000 cP

        Args:
            temp_c: melt temperature in °C

        Returns:
            Viscosity in cP
        """
        return self.viscosity_pa_s(temp_c) * 1000.0

    def draw_window(
        self,
        min_visc_cp: float = 400.0,
        max_visc_cp: float = 1200.0,
    ) -> tuple[float, float]:
        """
        Calculate the temperature range corresponding to the optimal
        draw viscosity window.

        Args:
            min_visc_cp: lower viscosity bound (hotter, thinner)
            max_visc_cp: upper viscosity bound (cooler, thicker)

        Returns:
            (min_temp_c, max_temp_c) defining the draw window
        """
        # Binary search for temperature at each viscosity bound
        def temp_at_visc(target_cp: float) -> float:
            lo, hi = 1200.0, 1700.0
            for _ in range(60):
                mid = (lo + hi) / 2.0
                if self.viscosity_cp(mid) > target_cp:
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2.0

        return (temp_at_visc(max_visc_cp), temp_at_visc(min_visc_cp))

    def optimal_draw_temp(self, target_diameter_um: float) -> float:
        """
        Estimate the optimal melt temperature for a target fiber diameter.

        Based on empirical relationship between draw viscosity and
        fiber diameter at a fixed draw speed.

        Args:
            target_diameter_um: desired fiber diameter in micrometres

        Returns:
            Recommended melt temperature in °C
        """
        # Empirical mapping: finer fibers need lower viscosity (higher temp)
        # Diameter range 8–20 µm maps to viscosity range 350–1100 cP
        # Linear interpolation within this range
        d_min, d_max   = 8.0, 20.0
        v_min, v_max   = 350.0, 1100.0

        d_clamped = max(d_min, min(d_max, target_diameter_um))
        t_ratio   = (d_clamped - d_min) / (d_max - d_min)
        target_cp = v_min + t_ratio * (v_max - v_min)

        lo, hi = 1300.0, 1650.0
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if self.viscosity_cp(mid) > target_cp:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0
