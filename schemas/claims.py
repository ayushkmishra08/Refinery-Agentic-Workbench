"""Pydantic schema for engineering claims with strict predicate/unit validation.

Claims capture quantitative and qualitative engineering facts extracted from
source documents. Every claim requires evidence and provenance.

The claim categories and unit families enable deterministic validation —
e.g., a pressure claim must not have temperature units.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ClaimCategory(str, Enum):
    """Engineering claim categories."""
    # Pressure
    DESIGN_PRESSURE = "design_pressure"
    NORMAL_PRESSURE = "normal_pressure"
    MAXIMUM_PRESSURE = "maximum_pressure"
    MINIMUM_PRESSURE = "minimum_pressure"
    OPERATING_PRESSURE = "operating_pressure"
    TEST_PRESSURE = "test_pressure"
    RELIEF_PRESSURE = "relief_pressure"

    # Temperature
    DESIGN_TEMPERATURE = "design_temperature"
    NORMAL_TEMPERATURE = "normal_temperature"
    MAXIMUM_TEMPERATURE = "maximum_temperature"
    MINIMUM_TEMPERATURE = "minimum_temperature"
    OPERATING_TEMPERATURE = "operating_temperature"

    # Flow
    FLOW_RATE = "flow_rate"
    MASS_FLOW_RATE = "mass_flow_rate"
    VOLUMETRIC_FLOW_RATE = "volumetric_flow_rate"

    # Capacity and sizing
    CAPACITY = "capacity"
    POWER = "power"
    SPEED = "speed"
    DIAMETER = "diameter"
    LENGTH = "length"
    HEIGHT = "height"
    THICKNESS = "thickness"
    WEIGHT = "weight"
    VOLUME = "volume"
    AREA = "area"

    # Material
    MATERIAL = "material"
    MATERIAL_GRADE = "material_grade"
    CORROSION_ALLOWANCE = "corrosion_allowance"

    # Process
    FEED_RATE = "feed_rate"
    PRODUCT_RATE = "product_rate"
    YIELD = "yield"
    CUT_RANGE = "cut_range"
    SPECIFIC_GRAVITY = "specific_gravity"
    DENSITY = "density"
    VISCOSITY = "viscosity"
    MOLECULAR_WEIGHT = "molecular_weight"
    FLASH_POINT = "flash_point"
    POUR_POINT = "pour_point"

    # Operational
    OPERATING_RANGE = "operating_range"
    SETPOINT = "setpoint"
    ALARM_LIMIT = "alarm_limit"
    TRIP_LIMIT = "trip_limit"
    INTERLOCK_CONDITION = "interlock_condition"
    TRIP_CONDITION = "trip_condition"
    RELIEF_CONDITION = "relief_condition"

    # Ratings and conditions
    EQUIPMENT_RATING = "equipment_rating"
    DESIGN_CONDITION = "design_condition"
    OPERATING_CONDITION = "operating_condition"

    # Requirements
    STARTUP_REQUIREMENT = "startup_requirement"
    SHUTDOWN_REQUIREMENT = "shutdown_requirement"
    SAFETY_REQUIREMENT = "safety_requirement"
    MAINTENANCE_REQUIREMENT = "maintenance_requirement"
    INSPECTION_INTERVAL = "inspection_interval"
    SAMPLING_FREQUENCY = "sampling_frequency"
    CHEMICAL_HANDLING_REQUIREMENT = "chemical_handling_requirement"
    PPE_REQUIREMENT = "ppe_requirement"

    # Document
    REVISION = "revision"
    STATUS = "status"

    # Dependencies
    PROCESS_DEPENDENCY = "process_dependency"
    OPERATIONAL_RESTRICTION = "operational_restriction"

    # Generic
    GENERIC_PROPERTY = "generic_property"


class UnitFamily(str, Enum):
    """Families of engineering units for validation."""
    PRESSURE = "pressure"
    TEMPERATURE = "temperature"
    MASS_FLOW = "mass_flow"
    VOLUMETRIC_FLOW = "volumetric_flow"
    POWER = "power"
    SPEED = "speed"
    LENGTH = "length"
    MASS = "mass"
    DENSITY = "density"
    VISCOSITY = "viscosity"
    TIME = "time"
    DIMENSIONLESS = "dimensionless"
    PERCENTAGE = "percentage"
    TEXT = "text"
    UNKNOWN = "unknown"


# Mapping from claim categories to their valid unit families
CLAIM_UNIT_RULES: dict[ClaimCategory, set[UnitFamily]] = {
    # Pressure claims → only pressure units
    ClaimCategory.DESIGN_PRESSURE: {UnitFamily.PRESSURE},
    ClaimCategory.NORMAL_PRESSURE: {UnitFamily.PRESSURE},
    ClaimCategory.MAXIMUM_PRESSURE: {UnitFamily.PRESSURE},
    ClaimCategory.MINIMUM_PRESSURE: {UnitFamily.PRESSURE},
    ClaimCategory.OPERATING_PRESSURE: {UnitFamily.PRESSURE},
    ClaimCategory.TEST_PRESSURE: {UnitFamily.PRESSURE},
    ClaimCategory.RELIEF_PRESSURE: {UnitFamily.PRESSURE},

    # Temperature claims → only temperature units
    ClaimCategory.DESIGN_TEMPERATURE: {UnitFamily.TEMPERATURE},
    ClaimCategory.NORMAL_TEMPERATURE: {UnitFamily.TEMPERATURE},
    ClaimCategory.MAXIMUM_TEMPERATURE: {UnitFamily.TEMPERATURE},
    ClaimCategory.MINIMUM_TEMPERATURE: {UnitFamily.TEMPERATURE},
    ClaimCategory.OPERATING_TEMPERATURE: {UnitFamily.TEMPERATURE},

    # Flow claims
    ClaimCategory.FLOW_RATE: {UnitFamily.MASS_FLOW, UnitFamily.VOLUMETRIC_FLOW},
    ClaimCategory.MASS_FLOW_RATE: {UnitFamily.MASS_FLOW},
    ClaimCategory.VOLUMETRIC_FLOW_RATE: {UnitFamily.VOLUMETRIC_FLOW},
    ClaimCategory.FEED_RATE: {UnitFamily.MASS_FLOW, UnitFamily.VOLUMETRIC_FLOW},
    ClaimCategory.PRODUCT_RATE: {UnitFamily.MASS_FLOW, UnitFamily.VOLUMETRIC_FLOW},

    # Sizing
    ClaimCategory.POWER: {UnitFamily.POWER},
    ClaimCategory.SPEED: {UnitFamily.SPEED},
    ClaimCategory.DIAMETER: {UnitFamily.LENGTH},
    ClaimCategory.LENGTH: {UnitFamily.LENGTH},
    ClaimCategory.HEIGHT: {UnitFamily.LENGTH},
    ClaimCategory.THICKNESS: {UnitFamily.LENGTH},
    ClaimCategory.WEIGHT: {UnitFamily.MASS},
    ClaimCategory.CAPACITY: {UnitFamily.MASS_FLOW, UnitFamily.VOLUMETRIC_FLOW, UnitFamily.MASS},

    # Process properties
    ClaimCategory.SPECIFIC_GRAVITY: {UnitFamily.DIMENSIONLESS},
    ClaimCategory.DENSITY: {UnitFamily.DENSITY},
    ClaimCategory.YIELD: {UnitFamily.PERCENTAGE, UnitFamily.DIMENSIONLESS},
    ClaimCategory.VISCOSITY: {UnitFamily.VISCOSITY},

    # Text-based claims (no numeric unit needed)
    ClaimCategory.MATERIAL: {UnitFamily.TEXT},
    ClaimCategory.MATERIAL_GRADE: {UnitFamily.TEXT},
    ClaimCategory.CORROSION_ALLOWANCE: {UnitFamily.LENGTH},
}


# Valid unit strings for each family
VALID_UNITS: dict[UnitFamily, set[str]] = {
    UnitFamily.PRESSURE: {
        "bar", "barg", "bara", "psi", "psig", "psia",
        "kPa", "MPa", "Pa", "kg/cm2", "kg/cm²",
        "atm", "mmHg", "mmWC", "mbar",
    },
    UnitFamily.TEMPERATURE: {"°C", "°F", "K", "C", "F"},
    UnitFamily.MASS_FLOW: {
        "kg/h", "kg/hr", "kg/s", "t/h", "t/hr",
        "MT/h", "MTPA", "TPA", "lb/h", "lb/hr",
    },
    UnitFamily.VOLUMETRIC_FLOW: {
        "m3/h", "m³/h", "m3/hr", "l/h", "l/min",
        "gpm", "GPM", "bbl/d", "BPD", "BPSD",
        "Nm3/h", "Nm³/h", "SCFM",
    },
    UnitFamily.POWER: {"kW", "MW", "W", "HP", "hp", "BHP", "bhp"},
    UnitFamily.SPEED: {"rpm", "RPM", "rad/s"},
    UnitFamily.LENGTH: {
        "mm", "cm", "m", "km", "in", "inch", "ft", "feet",
        "μm", "micron",
    },
    UnitFamily.MASS: {"kg", "g", "t", "MT", "lb", "ton", "tonne"},
    UnitFamily.DENSITY: {"kg/m3", "kg/m³", "g/cm3", "g/cm³", "g/cc", "lb/ft3"},
    UnitFamily.VISCOSITY: {"cSt", "cP", "mPa.s", "Pa.s"},
    UnitFamily.DIMENSIONLESS: {"", "-", "dimensionless"},
    UnitFamily.PERCENTAGE: {"%", "wt%", "vol%", "mol%"},
    UnitFamily.TEXT: {""},
}


class EngineeringClaim(BaseModel):
    """A single engineering claim with full provenance.

    Every claim is a (subject, predicate, value) triple with evidence
    traceable to a specific document, page, and source text.
    """
    claim_id: str = Field(description="Unique claim identifier")
    subject: str = Field(description="Entity this claim is about (e.g., 'P-101')")
    subject_uid: str = Field(default="", description="UID of the subject entity in the graph")
    predicate: ClaimCategory = Field(description="What property is being claimed")
    predicate_raw: str = Field(default="", description="Predicate label exactly as the model produced it")
    value: str = Field(description="The claimed value (numeric or text)")
    unit: str = Field(default="", description="Engineering unit if applicable")
    unit_family: UnitFamily = Field(default=UnitFamily.UNKNOWN)

    # Provenance
    evidence: str = Field(description="Source text supporting this claim")
    page: int = Field(description="Page number where evidence was found")
    section: str = Field(default="")
    document_id: str = Field(default="")
    source_document: str = Field(default="", description="Source document filename")
    chunk_id: str = Field(default="")

    # Quality
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_from_table: bool = Field(default=False, description="Whether this was extracted from a table")
    table_id: str | None = Field(default=None)

    # Grounding / source
    grounding: str = Field(default="strong", description="strong = one sentence/row names subject and value; weak = apart")
    source: str = Field(default="llm_pass1", description="llm_pass1 | llm_pass2 | rule | table")
    sentence_index: int | None = Field(default=None)

    # Conflict tracking
    has_conflict: bool = Field(default=False)
    conflicting_claim_ids: list[str] = Field(default_factory=list)
