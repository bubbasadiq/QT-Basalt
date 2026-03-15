"""
Recipe Loader

Loads fiber recipe YAML files from control/recipes/ into
FiberRecipe objects used by the controller.

Recipes can also be created programmatically and passed directly
to the controller — the loader is the convenience interface for
the named recipes that ship with the platform.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from qutlas.schema import FiberClass, FiberRecipe

logger = logging.getLogger(__name__)

# Default recipe directory — relative to repo root
DEFAULT_RECIPE_DIR = Path("control/recipes")


class RecipeLoader:
    """
    Loads and caches FiberRecipe objects from YAML files.

    Falls back to built-in default recipes when YAML files
    are not available (e.g. in test environments).
    """

    # Built-in defaults match control/recipes/*.yaml
    DEFAULTS: dict[str, FiberRecipe] = {
        "structural": FiberRecipe(
            name="structural", fiber_class=FiberClass.STRUCTURAL,
            description="Structural reinforcement fiber",
            target_tensile_gpa=2.9, target_modulus_gpa=85.0,
            target_diameter_um=13.0, target_thermal_c=650.0,
            tol_tensile_gpa=0.15, tol_modulus_gpa=3.0,
            tol_diameter_um=1.5, tol_thermal_c=25.0,
            initial_temp_c=1480.0, initial_draw_speed_ms=12.0,
            initial_airflow_lpm=48.0,
            max_temp_c=1560.0, min_temp_c=1420.0, max_draw_speed_ms=18.0,
        ),
        "high_temperature": FiberRecipe(
            name="high_temperature", fiber_class=FiberClass.HIGH_TEMPERATURE,
            description="High temperature insulation fiber",
            target_tensile_gpa=2.5, target_modulus_gpa=78.0,
            target_diameter_um=11.0, target_thermal_c=760.0,
            tol_tensile_gpa=0.2, tol_modulus_gpa=4.0,
            tol_diameter_um=1.5, tol_thermal_c=20.0,
            initial_temp_c=1540.0, initial_draw_speed_ms=9.0,
            initial_airflow_lpm=35.0,
            max_temp_c=1600.0, min_temp_c=1480.0, max_draw_speed_ms=14.0,
        ),
        "electrical_insulation": FiberRecipe(
            name="electrical_insulation",
            fiber_class=FiberClass.ELECTRICAL,
            description="Electrical insulation fiber",
            target_tensile_gpa=2.6, target_modulus_gpa=80.0,
            target_diameter_um=10.0, target_thermal_c=620.0,
            tol_tensile_gpa=0.2, tol_modulus_gpa=4.0,
            tol_diameter_um=1.0, tol_thermal_c=30.0,
            initial_temp_c=1460.0, initial_draw_speed_ms=14.0,
            initial_airflow_lpm=55.0,
            max_temp_c=1520.0, min_temp_c=1400.0, max_draw_speed_ms=20.0,
        ),
        "corrosion_resistant": FiberRecipe(
            name="corrosion_resistant",
            fiber_class=FiberClass.CORROSION_RESISTANT,
            description="Corrosion resistant fiber",
            target_tensile_gpa=2.7, target_modulus_gpa=82.0,
            target_diameter_um=14.0, target_thermal_c=640.0,
            tol_tensile_gpa=0.2, tol_modulus_gpa=4.0,
            tol_diameter_um=2.0, tol_thermal_c=30.0,
            initial_temp_c=1470.0, initial_draw_speed_ms=11.0,
            initial_airflow_lpm=44.0,
            max_temp_c=1540.0, min_temp_c=1410.0, max_draw_speed_ms=16.0,
        ),
        "precision_structural": FiberRecipe(
            name="precision_structural",
            fiber_class=FiberClass.PRECISION,
            description="Precision structural fiber",
            target_tensile_gpa=3.1, target_modulus_gpa=90.0,
            target_diameter_um=9.0, target_thermal_c=660.0,
            tol_tensile_gpa=0.1, tol_modulus_gpa=2.5,
            tol_diameter_um=0.8, tol_thermal_c=25.0,
            initial_temp_c=1500.0, initial_draw_speed_ms=16.0,
            initial_airflow_lpm=60.0,
            max_temp_c=1570.0, min_temp_c=1440.0, max_draw_speed_ms=22.0,
        ),
    }

    def __init__(self, recipe_dir: Path | str | None = None) -> None:
        self.recipe_dir = Path(recipe_dir or DEFAULT_RECIPE_DIR)
        self._cache: dict[str, FiberRecipe] = {}

    def load(self, name: str) -> FiberRecipe:
        """
        Load a recipe by name.

        Tries YAML file first, falls back to built-in defaults.

        Args:
            name: recipe name (e.g. "structural")

        Returns:
            FiberRecipe

        Raises:
            ValueError: if recipe not found in files or defaults
        """
        if name in self._cache:
            return self._cache[name]

        # Try YAML file
        recipe = self._load_yaml(name)
        if recipe is not None:
            self._cache[name] = recipe
            return recipe

        # Fall back to defaults
        if name in self.DEFAULTS:
            logger.debug(f"RecipeLoader: using built-in default for '{name}'")
            self._cache[name] = self.DEFAULTS[name]
            return self.DEFAULTS[name]

        available = list(self.DEFAULTS.keys())
        raise ValueError(
            f"Recipe '{name}' not found. Available: {available}"
        )

    def list_available(self) -> list[str]:
        """Return names of all available recipes."""
        names = set(self.DEFAULTS.keys())
        if self.recipe_dir.exists():
            for f in self.recipe_dir.glob("*.yaml"):
                names.add(f.stem)
        return sorted(names)

    def _load_yaml(self, name: str) -> Optional[FiberRecipe]:
        """Attempt to load a recipe from a YAML file."""
        path = self.recipe_dir / f"{name}.yaml"
        if not path.exists():
            return None
        try:
            import yaml   # type: ignore[import]
            with open(path) as f:
                data = yaml.safe_load(f)
            return FiberRecipe(
                name                  = data["name"],
                fiber_class           = FiberClass(data["fiber_class"]),
                description           = data.get("description", ""),
                target_tensile_gpa    = float(data["target_tensile_gpa"]),
                target_modulus_gpa    = float(data["target_modulus_gpa"]),
                target_diameter_um    = float(data["target_diameter_um"]),
                target_thermal_c      = float(data["target_thermal_c"]),
                tol_tensile_gpa       = float(data.get("tol_tensile_gpa", 0.15)),
                tol_modulus_gpa       = float(data.get("tol_modulus_gpa", 3.0)),
                tol_diameter_um       = float(data.get("tol_diameter_um", 1.5)),
                tol_thermal_c         = float(data.get("tol_thermal_c", 25.0)),
                initial_temp_c        = float(data.get("initial_temp_c", 1480.0)),
                initial_draw_speed_ms = float(data.get("initial_draw_speed_ms", 12.0)),
                initial_airflow_lpm   = float(data.get("initial_airflow_lpm", 48.0)),
                max_temp_c            = float(data.get("max_temp_c", 1560.0)),
                min_temp_c            = float(data.get("min_temp_c", 1420.0)),
                max_draw_speed_ms     = float(data.get("max_draw_speed_ms", 18.0)),
            )
        except Exception as e:
            logger.warning(f"Failed to load recipe YAML '{path}': {e}")
            return None
