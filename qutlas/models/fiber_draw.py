"""
Fiber Draw Mechanics Model

Models the relationship between draw speed, melt viscosity,
bushing geometry, and the resulting fiber diameter.

Physics basis:
  - Mass conservation: melt flow rate = fiber production rate
  - Fluid mechanics of viscous flow through bushing nozzle
    (Hagen-Poiseuille flow regime)
  - Fiber attenuation: ratio of bushing tip area to fiber cross-section
  - Source: Loewenstein (1993) "The Manufacturing Technology of
    Continuous Glass Fibres", 3rd ed., Elsevier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BushingConfig:
    """
    Physical configuration of the fiber extrusion bushing.
    A bushing is a platinum-rhodium plate with precision nozzle holes.
    """
    nozzle_count:      int   = 200       # number of fiber-forming nozzles
    nozzle_diameter_m: float = 2.0e-3    # 2 mm nozzle tip diameter
    nozzle_length_m:   float = 4.0e-3    # 4 mm nozzle length
    bushing_temp_c:    float = 1480.0    # bushing operating temperature °C


@dataclass
class DrawState:
    """Current state of the fiber draw process."""
    fiber_diameter_um:  float  # µm — actual fiber diameter
    draw_tension_n:     float  # N — draw tension
    attenuation_ratio:  float  # dimensionless — bushing/fiber area ratio
    mass_flow_g_min:    float  # g/min — total melt throughput


class FiberDrawModel:
    """
    Models fiber diameter as a function of draw speed and melt viscosity.

    The core relationship is mass conservation:
        A_fiber * v_draw = A_nozzle * v_melt

    Where v_melt (melt exit velocity) depends on viscosity and
    applied pressure (gravity + head pressure).

    Usage:
        draw = FiberDrawModel()
        state = draw.step(
            draw_speed_ms=12.0,
            viscosity_cp=750.0,
            temp_c=1480.0,
        )
        print(state.fiber_diameter_um)   # → ~13 µm
    """

    def __init__(self, config: BushingConfig | None = None) -> None:
        self.config = config or BushingConfig()

    def step(
        self,
        draw_speed_ms: float,
        viscosity_cp:  float,
        temp_c:        float,
        head_pressure_pa: float = 800.0,
    ) -> DrawState:
        """
        Calculate fiber draw state for given process conditions.

        Args:
            draw_speed_ms:    fiber draw speed in m/s
            viscosity_cp:     melt dynamic viscosity in cP
            temp_c:           melt temperature in °C (used for density)
            head_pressure_pa: hydrostatic head pressure from melt column (Pa)

        Returns:
            DrawState with fiber diameter and process metrics
        """
        cfg  = self.config
        visc = viscosity_cp * 1e-3  # convert cP → Pa·s

        # Basalt melt density at temperature (empirical, ~2650 kg/m³ at 1480°C)
        density = self._melt_density(temp_c)

        # Nozzle geometry
        r_nozzle = cfg.nozzle_diameter_m / 2.0
        a_nozzle = math.pi * r_nozzle ** 2

        # Hagen-Poiseuille flow through nozzle:
        # Q = (π r⁴ ΔP) / (8 η L)
        # Total pressure = gravity head + applied head
        gravity_pressure = density * 9.81 * 0.04  # 40mm melt depth typical
        delta_p          = gravity_pressure + head_pressure_pa

        q_nozzle = (
            math.pi * r_nozzle ** 4 * delta_p /
            (8.0 * visc * cfg.nozzle_length_m)
        )  # m³/s per nozzle

        # Melt exit velocity from single nozzle
        v_melt = q_nozzle / a_nozzle  # m/s

        # Mass conservation → fiber cross-section
        # a_nozzle * v_melt = a_fiber * v_draw
        a_fiber = a_nozzle * v_melt / max(draw_speed_ms, 0.01)

        # Fiber diameter from cross-section area
        r_fiber = math.sqrt(a_fiber / math.pi)
        diameter_um = r_fiber * 2.0 * 1e6  # convert m → µm

        # Attenuation ratio
        attenuation = a_nozzle / max(a_fiber, 1e-20)

        # Draw tension estimate (viscous drag model)
        # F ≈ 3π η v_draw d_fiber (Stokes drag analog)
        tension_n = 3.0 * math.pi * visc * draw_speed_ms * r_fiber * 2.0

        # Mass flow rate (total across all nozzles)
        mass_flow = (
            q_nozzle * cfg.nozzle_count * density * 60.0 * 1000.0
        )  # g/min

        return DrawState(
            fiber_diameter_um = max(1.0, min(50.0, diameter_um)),
            draw_tension_n    = tension_n,
            attenuation_ratio = attenuation,
            mass_flow_g_min   = mass_flow,
        )

    def diameter_at_speed(
        self,
        draw_speed_ms: float,
        viscosity_cp:  float,
        temp_c:        float = 1480.0,
    ) -> float:
        """
        Convenience method — returns fiber diameter in µm.

        Args:
            draw_speed_ms: draw speed in m/s
            viscosity_cp:  melt viscosity in cP
            temp_c:        melt temperature in °C

        Returns:
            Predicted fiber diameter in µm
        """
        return self.step(draw_speed_ms, viscosity_cp, temp_c).fiber_diameter_um

    def speed_for_diameter(
        self,
        target_diameter_um: float,
        viscosity_cp:       float,
        temp_c:             float = 1480.0,
        tolerance_um:       float = 0.1,
    ) -> float:
        """
        Invert the draw model: find the draw speed needed to produce
        a target fiber diameter at given viscosity and temperature.

        Args:
            target_diameter_um: desired fiber diameter in µm
            viscosity_cp:       melt viscosity in cP
            temp_c:             melt temperature in °C
            tolerance_um:       convergence tolerance in µm

        Returns:
            Required draw speed in m/s
        """
        lo, hi = 0.5, 40.0
        for _ in range(64):
            mid  = (lo + hi) / 2.0
            d    = self.diameter_at_speed(mid, viscosity_cp, temp_c)
            if d > target_diameter_um:
                lo = mid   # faster draw → thinner fiber
            else:
                hi = mid
            if abs(d - target_diameter_um) < tolerance_um:
                break
        return (lo + hi) / 2.0

    @staticmethod
    def _melt_density(temp_c: float) -> float:
        """
        Basalt melt density as a function of temperature.
        Linear fit to measured data, valid 1400–1600°C.
        ~2650 kg/m³ at 1480°C, decreasing ~0.5 kg/m³ per °C.
        """
        return 2650.0 - 0.5 * (temp_c - 1480.0)
